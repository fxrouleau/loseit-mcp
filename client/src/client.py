"""High-level LoseIt client API."""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Optional

from .auth import Auth
from .bundle import (
    CaloriesEntry,
    FoodMeasureId,
    IngredientSpec,
    LOSEIT_EPOCH,
    MealType,
    build_add_calories_bundle,
    build_create_recipe_bundle,
    build_delete_log_bundle,
    build_delete_recipe_bundle,
    build_log_food_bundle,
    daily_log_entry,
    new_uuid16,
    now_ms,
    parse_bundle_response,
)
from .db import DEFAULT_CACHE, DailyLogState, LogRow, UserDatabase
from .food_search import Food, decode_food, decode_food_search_response
from .transport import Transport


@dataclass
class LoggedEntry:
    entry_uuid: bytes
    food_uuid: bytes
    name: str
    calories: float
    meal: MealType
    server_ack_txn_ids: list[int]
    raw_response_fields: list[int]


def _local_scale(food, *, servings: float | None, quantity: float | None) -> float:
    """Resolve a `(servings, quantity)` pair into a single multiplier on
    a local food's stored last-used serving.

    * `servings`: number of standard servings → multiplier is just N.
      Matches the LoseIt app picker: "2" of a 61g serving = 122 grams.
    * `quantity`: explicit raw amount in the food's measure unit, used
      when the caller wants e.g. "exactly 200 grams". The multiplier is
      `quantity / last_serving_quantity`.
    * Neither: 1 serving (the food's last-used quantity).
    """
    last_q = food.last_serving_quantity or 1.0
    if servings is not None:
        return float(servings)
    if quantity is not None:
        return float(quantity) / last_q
    return 1.0


class LoseItClient:
    """Minimal LoseIt client. Covers:

    - initial login via captured tokens (or password+captcha)
    - refresh (no captcha)
    - Add Calories one-shot logging
    - delete a log entry by uuid
    - barcode lookup

    For anything catalog-based (search food DB, log existing foods),
    download the user database via `transport.get_user_database()` and
    query the resulting SQLite file — that's what the native app does.
    """

    def __init__(self, auth: Auth | None = None) -> None:
        self.auth = auth or Auth()
        self.transport = Transport(self.auth)
        # Monotonic ms counter used for sync_token. The server treats it as
        # an opaque watermark and just echoes it forward; using local time
        # matches what the native app sends.
        self._last_sync_token = int(time.time() * 1000) - 1000
        self._db: UserDatabase | None = None

    # ---- database snapshot --------------------------------------

    def refresh_database(self) -> UserDatabase:
        """Download the latest user database snapshot and open it."""
        self._db = UserDatabase.download(self.transport)
        return self._db

    def database(self, *, refresh: bool = True) -> UserDatabase:
        """Return the user database. Downloads fresh every call by default
        since that's the only way to see updates from other devices or
        mutations we've just sent."""
        if refresh or self._db is None:
            self._db = UserDatabase.download(self.transport)
        return self._db

    # ---- helpers -------------------------------------------------

    @property
    def user_id(self) -> int:
        tokens = self.auth.ensure_fresh()
        return tokens.user_id

    # ---- daily banner delta -------------------------------------

    def _build_daily_log_delta(
        self,
        when: dt.date | None,
        delta_calories: float,
    ) -> bytes | None:
        """Build a DailyLogEntry update that reflects `delta_calories`
        being added to the given day's banner total.

        The native Android app re-sends DailyLogEntry on every food
        mutation. Without it, DailyLogEntries.FoodCalories stays stuck
        at whatever value the native app last wrote, so the "X cal
        today" banner at the top of the app drifts out of sync with
        the actual log entries.

        We refresh the database snapshot so the read of FoodCalories
        reflects any mutations made since our last call (including
        from the native app). If no DailyLogEntries row exists for the
        target day yet (first-ever log of the day), we seed from the
        most recent row's profile fields.
        """
        day = when or dt.date.today()
        db = self.database(refresh=True)
        state = db.get_daily_log_state(day)
        if state is None:
            template = db.get_most_recent_daily_log_state()
            if template is None:
                return None
            state = DailyLogState(
                date_day=(day - LOSEIT_EPOCH).days,
                current_weight=template.current_weight,
                current_eer=template.current_eer,
                current_activity_level=template.current_activity_level,
                budget_calories=template.budget_calories,
                food_calories=0.0,
                exercise_calories=0.0,
            )
        new_food_calories = max(0.0, state.food_calories + delta_calories)
        return daily_log_entry(
            date_day=state.date_day,
            budget_calories=state.budget_calories,
            weight=state.current_weight,
            eer=state.current_eer,
            activity_level=state.current_activity_level,
            food_calories=new_food_calories,
            exercise_calories=state.exercise_calories,
            last_updated_ms=now_ms(),
        )

    def _existing_entry_calories(self, entry_uuid: bytes) -> float:
        """Look up an existing food log entry's calories (for computing
        the delta on edit/delete). Returns 0.0 if not found."""
        db = self.database(refresh=False)
        row = db._con.execute(  # type: ignore[attr-defined]
            "SELECT Calories FROM FoodLogEntries WHERE UniqueId = ? AND Deleted = 0",
            (entry_uuid,),
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def _next_sync_token(self) -> int:
        t = max(int(time.time() * 1000) - 1000, self._last_sync_token + 1)
        self._last_sync_token = t
        return t

    # ---- mutations -----------------------------------------------

    def log_calories(
        self,
        *,
        name: str,
        calories: float,
        meal: MealType = MealType.SNACKS,
        fat: float = 0.0,
        carbohydrate: float = 0.0,
        protein: float = 0.0,
        day: Optional[dt.date] = None,
    ) -> LoggedEntry:
        """Log a one-off calorie entry with optional macros (Add Calories)."""
        entry = CaloriesEntry(
            name=name,
            calories=calories,
            fat=fat,
            carbohydrate=carbohydrate,
            protein=protein,
            meal=meal,
            day=day,
        )
        daily = self._build_daily_log_delta(day, float(calories))
        bundle = build_add_calories_bundle(
            entry,
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
            daily_log_entry_bytes=daily,
        )
        resp = self.transport.post_transaction_bundle(bundle)
        parsed = parse_bundle_response(resp)
        return LoggedEntry(
            entry_uuid=entry.entry_uuid,
            food_uuid=entry.food_uuid,
            name=entry.name,
            calories=entry.calories,
            meal=entry.meal,
            server_ack_txn_ids=parsed["ack_txn_ids"],
            raw_response_fields=parsed["raw_fields"],
        )

    def edit_calories(
        self,
        *,
        entry_uuid: bytes,
        food_uuid: bytes,
        name: str,
        calories: float,
        meal: MealType,
        fat: float = 0.0,
        carbohydrate: float = 0.0,
        protein: float = 0.0,
        day: Optional[dt.date] = None,
    ) -> LoggedEntry:
        """Edit a calories entry in place. Reuses the original entry_uuid
        and food_uuid so the server upserts rather than inserting a new row.
        """
        entry = CaloriesEntry(
            name=name,
            calories=calories,
            fat=fat,
            carbohydrate=carbohydrate,
            protein=protein,
            meal=meal,
            day=day,
            entry_uuid=entry_uuid,
            food_uuid=food_uuid,
        )
        prev_calories = self._existing_entry_calories(entry_uuid)
        daily = self._build_daily_log_delta(day, float(calories) - prev_calories)
        bundle = build_add_calories_bundle(
            entry,
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
            daily_log_entry_bytes=daily,
        )
        resp = self.transport.post_transaction_bundle(bundle)
        parsed = parse_bundle_response(resp)
        return LoggedEntry(
            entry_uuid=entry.entry_uuid,
            food_uuid=entry.food_uuid,
            name=entry.name,
            calories=entry.calories,
            meal=entry.meal,
            server_ack_txn_ids=parsed["ack_txn_ids"],
            raw_response_fields=parsed["raw_fields"],
        )

    def edit_food_entry(
        self,
        *,
        entry_uuid: bytes,
        food_uuid: bytes,
        meal: MealType,
        servings: float | None = None,
        quantity: float | None = None,
        day: Optional[dt.date] = None,
    ) -> LoggedEntry:
        """Edit an existing food log entry. Reuses the entry_uuid so the
        server upserts rather than inserting a new row.

        See `log_food` for the difference between `servings` and `quantity`.
        """
        from .bundle import build_log_food_bundle

        if servings is not None and quantity is not None:
            raise ValueError("pass servings OR quantity, not both")

        db = self.database(refresh=False)
        food = db.get_food_by_uuid(food_uuid)
        if food is None:
            raise KeyError(
                f"food {food_uuid.hex()} not in local db; call refresh_database()"
            )
        scale = _local_scale(food, servings=servings, quantity=quantity)
        raw_amount = (food.last_serving_quantity or 1.0) * scale
        new_calories = food.last_serving_calories * scale
        prev_calories = self._existing_entry_calories(entry_uuid)
        daily = self._build_daily_log_delta(day, new_calories - prev_calories)
        bundle, _ = build_log_food_bundle(
            food_uuid=food.food_uuid,
            food_name=food.name,
            food_product_name=food.product_name,
            measure_id=food.measure_id,
            measure_singular=food.measure_name,
            measure_plural=food.measure_name_plural,
            serving_quantity=raw_amount,
            serving_base_units=raw_amount,
            calories=new_calories,
            fat=food.last_serving_fat * scale,
            carbohydrate=food.last_serving_carbohydrate * scale,
            protein=food.last_serving_protein * scale,
            meal=int(meal),
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
            day=day,
            entry_uuid=entry_uuid,
            daily_log_entry_bytes=daily,
        )
        resp = self.transport.post_transaction_bundle(bundle)
        parsed = parse_bundle_response(resp)
        return LoggedEntry(
            entry_uuid=entry_uuid,
            food_uuid=food.food_uuid,
            name=food.name,
            calories=food.last_serving_calories * scale,
            meal=meal,
            server_ack_txn_ids=parsed["ack_txn_ids"],
            raw_response_fields=parsed["raw_fields"],
        )

    def log_food(
        self,
        *,
        food_uuid: bytes,
        meal: MealType,
        servings: float | None = None,
        quantity: float | None = None,
        measure_id: int | None = None,
        day: Optional[dt.date] = None,
    ) -> LoggedEntry:
        """Log an existing food from the user's library by uuid.

        Args:
            food_uuid: the food's uuid.
            meal: which meal to log under.
            servings: number of standard servings to log. This is the
                "natural" parameter — `servings=2` of a food whose
                last-used serving was "1 Each" gives "2 Each", and
                `servings=2` of a food whose last-used serving was "61
                Grams" gives "122 Grams". Use this for "log 2 carrots".
            quantity: explicit raw amount in the food's measure unit.
                Bypasses servings math. Use for "log exactly 200 grams
                of rice" when the food is stored in grams.
            measure_id: optional unit override. When set, the client
                looks up the food in the catalog under the requested
                measure (e.g. GRAM, CUP) and uses that serving as the
                basis. servings/quantity then apply to the new unit.
            day: ISO date or None for today.

        servings and quantity are mutually exclusive. If neither is
        given, the food's last-used serving is logged once (servings=1).
        """
        if servings is not None and quantity is not None:
            raise ValueError("pass servings OR quantity, not both")

        db = self.database(refresh=False)
        food = db.get_food_by_uuid(food_uuid)
        if food is None:
            raise KeyError(f"food {food_uuid.hex()} not in local db; call refresh_database()?")

        if measure_id is not None and measure_id != food.measure_id:
            # User wants a unit the local library doesn't store. Try to
            # find the food in the catalog and pick the matching serving.
            return self._log_food_alt_unit(
                food=food, meal=meal,
                servings=servings, quantity=quantity,
                measure_id=measure_id, day=day,
            )

        scale = _local_scale(food, servings=servings, quantity=quantity)
        raw_amount = (food.last_serving_quantity or 1.0) * scale
        new_calories = food.last_serving_calories * scale
        daily = self._build_daily_log_delta(day, new_calories)

        bundle, entry_uuid = build_log_food_bundle(
            food_uuid=food.food_uuid,
            food_name=food.name,
            food_product_name=food.product_name,
            measure_id=food.measure_id,
            measure_singular=food.measure_name,
            measure_plural=food.measure_name_plural,
            serving_quantity=raw_amount,
            serving_base_units=raw_amount,
            calories=new_calories,
            fat=food.last_serving_fat * scale,
            carbohydrate=food.last_serving_carbohydrate * scale,
            protein=food.last_serving_protein * scale,
            meal=int(meal),
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
            day=day,
            daily_log_entry_bytes=daily,
        )
        resp = self.transport.post_transaction_bundle(bundle)
        parsed = parse_bundle_response(resp)
        return LoggedEntry(
            entry_uuid=entry_uuid,
            food_uuid=food.food_uuid,
            name=food.name,
            calories=new_calories,
            meal=meal,
            server_ack_txn_ids=parsed["ack_txn_ids"],
            raw_response_fields=parsed["raw_fields"],
        )

    def _log_food_alt_unit(
        self,
        *,
        food,
        meal: MealType,
        servings: float | None,
        quantity: float | None,
        measure_id: int,
        day: Optional[dt.date],
    ) -> LoggedEntry:
        """Fallback path for log_food when the caller asks for a unit the
        local library doesn't have. Hits the catalog to find the SAME
        food (by uuid) under a different measure.

        We deliberately do NOT fall back to a different food when the
        catalog search misses — that previously caused silent food swaps
        (e.g. requesting 'Carrot, Whole' returned 'Carrot Stick Whole').
        Caller should use search_catalog directly to pick a different
        food if no exact match exists.
        """
        candidates = self.search_catalog(food.name, limit=20)
        match = next((f for f in candidates if f.unique_id == food.food_uuid), None)
        if match is None:
            raise LookupError(
                f"food {food.name!r} ({food.food_uuid.hex()}) is not in "
                f"the catalog under that uuid, so it can't be re-logged in "
                f"a different measure. Use search_catalog directly to find "
                f"a matching food with the unit you want."
            )
        serving_index = next(
            (i for i, s in enumerate(match.servings) if s.measure_id == measure_id),
            None,
        )
        if serving_index is None:
            available = [s.measure_id for s in match.servings]
            raise LookupError(
                f"food {food.name!r} has no serving with measure_id={measure_id}; "
                f"available: {available}"
            )
        return self.log_food_from_catalog(
            match, meal=meal,
            servings=servings, quantity=quantity,
            serving_index=serving_index, day=day,
        )

    def delete_log_entry(
        self,
        *,
        entry_uuid: bytes,
        food_uuid: bytes,
        food_name: str,
        meal: MealType,
        calories: float,
        fat: float = 0.0,
        carbohydrate: float = 0.0,
        protein: float = 0.0,
        day: Optional[dt.date] = None,
        measure_id: int = FoodMeasureId.EACH,
        measure_singular: str = "Each",
        measure_plural: str = "Each",
    ) -> dict:
        """Delete an existing log entry by re-sending it with the tombstone
        flag set. Also decrements the DailyLogEntries banner so the
        calorie total stays accurate."""
        daily = self._build_daily_log_delta(day, -float(calories))
        bundle = build_delete_log_bundle(
            entry_uuid=entry_uuid,
            food_uuid=food_uuid,
            food_name=food_name,
            meal=int(meal),
            calories=calories,
            fat=fat,
            carbohydrate=carbohydrate,
            protein=protein,
            day=day,
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
            measure_id=measure_id,
            measure_singular=measure_singular,
            measure_plural=measure_plural,
            daily_log_entry_bytes=daily,
        )
        resp = self.transport.post_transaction_bundle(bundle)
        return parse_bundle_response(resp)

    # ---- reads ---------------------------------------------------

    def get_day_log(
        self, day: dt.date | None = None, *, refresh: bool = True
    ) -> list[LogRow]:
        """Return all (non-deleted) log entries for the given day.

        Downloads the user database snapshot every call (LoseIt gateway
        doesn't expose a light GET endpoint). Pass `refresh=False` to read
        the last cached copy.
        """
        db = self.database(refresh=refresh)
        return db.get_day_log(day or dt.date.today())

    def search_foods(self, query: str, *, limit: int = 20, refresh: bool = True):
        """Free-text search over the user's food library (ActiveFoods)."""
        return self.database(refresh=refresh).search_foods(query, limit=limit)

    def search_recipes(self, query: str, *, limit: int = 20, refresh: bool = True):
        return self.database(refresh=refresh).search_recipes(query, limit=limit)

    # ---- recipe CRUD --------------------------------------------

    def create_recipe(
        self,
        name: str,
        ingredients: list[tuple[bytes, float]],
        *,
        total_servings: float = 1.0,
    ) -> bytes:
        """Create a recipe from a list of (food_uuid, quantity) tuples.

        Quantities are expressed in each ingredient's default (last-used)
        measure from the user's library. For example:

            apple = c.search_foods("Apple, Medium")[0]
            banana = c.search_foods("Banana, Medium")[0]
            recipe_uuid = c.create_recipe(
                "Morning bowl",
                [(apple.food_uuid, 1), (banana.food_uuid, 1)],
                total_servings=1,
            )

        Returns the new recipe's uuid so you can log or delete it later.
        """
        db = self.database(refresh=False)
        specs: list[IngredientSpec] = []
        for food_uuid, qty in ingredients:
            food = db.get_food_by_uuid(food_uuid)
            if food is None:
                raise KeyError(f"ingredient {food_uuid.hex()} not in local db")
            specs.append(
                IngredientSpec(
                    food_uuid=food.food_uuid,
                    food_name=food.name,
                    food_product_name=food.product_name,
                    measure_id=food.measure_id,
                    measure_singular=food.measure_name,
                    measure_plural=food.measure_name_plural or food.measure_name,
                    quantity=qty,
                    base_units=food.last_serving_base_units or 1.0,
                    calories=food.last_serving_calories,
                    fat=food.last_serving_fat,
                    carbohydrate=food.last_serving_carbohydrate,
                    protein=food.last_serving_protein,
                )
            )

        recipe_uuid = new_uuid16()
        bundle = build_create_recipe_bundle(
            recipe_uuid=recipe_uuid,
            recipe_name=name,
            ingredients=specs,
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
            total_servings=total_servings,
        )
        self.transport.post_transaction_bundle(bundle)
        return recipe_uuid

    def delete_recipe(self, *, recipe_uuid: bytes, recipe_name: str) -> dict:
        """Tombstone a recipe by uuid."""
        bundle = build_delete_recipe_bundle(
            recipe_uuid=recipe_uuid,
            recipe_name=recipe_name,
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
        )
        resp = self.transport.post_transaction_bundle(bundle)
        return parse_bundle_response(resp)

    def log_food_from_catalog(
        self,
        food: Food,
        *,
        meal: MealType,
        servings: float | None = None,
        quantity: float | None = None,
        serving_index: int = 0,
        day: Optional[dt.date] = None,
    ) -> LoggedEntry:
        """Log a food obtained from barcode lookup or catalog search.

        Picks `food.servings[serving_index]` as the unit basis. Barcode
        responses sometimes expose multiple servings (e.g. Skittles returns
        "27 Pieces" + "40 Grams") — pick the one you want.

        See `log_food` for the difference between `servings` and `quantity`:

        * servings=2 of a food whose serving is "61 Grams"  → 122 Grams
        * quantity=100 of a food whose serving is "61 Grams" → 100 Grams
        * default (neither set)                              → 1 serving
        """
        from .bundle import build_log_food_bundle, measure_labels
        if servings is not None and quantity is not None:
            raise ValueError("pass servings OR quantity, not both")
        if not food.servings:
            raise ValueError(f"food {food.name!r} has no serving sizes")
        if not (0 <= serving_index < len(food.servings)):
            raise IndexError(
                f"serving_index {serving_index} out of range; "
                f"food has {len(food.servings)} servings"
            )
        s = food.servings[serving_index]
        n = food.nutrients
        if n is None:
            raise ValueError(f"food {food.name!r} has no nutrient data")

        serving_size = s.size or 1.0
        if servings is not None:
            scale = servings
        elif quantity is not None:
            scale = quantity / serving_size
        else:
            scale = 1.0
        raw_amount = serving_size * scale
        new_calories = n.calories * scale
        daily = self._build_daily_log_delta(day, new_calories)

        # decode_food_serving_size already filled these, but fall back
        # through the enum table in case it encountered an unknown id.
        singular = s.measure_singular or measure_labels(s.measure_id)[0]
        plural = s.measure_plural or measure_labels(s.measure_id)[1] or singular

        bundle, entry_uuid = build_log_food_bundle(
            food_uuid=food.unique_id,
            food_name=food.name,
            food_product_name=food.name,
            measure_id=s.measure_id,
            measure_singular=singular,
            measure_plural=plural,
            serving_quantity=raw_amount,
            serving_base_units=raw_amount,
            calories=new_calories,
            fat=n.fat * scale,
            carbohydrate=n.carbohydrates * scale,
            protein=n.protein * scale,
            meal=int(meal),
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
            day=day,
            daily_log_entry_bytes=daily,
        )
        resp = self.transport.post_transaction_bundle(bundle)
        parsed = parse_bundle_response(resp)
        return LoggedEntry(
            entry_uuid=entry_uuid,
            food_uuid=food.unique_id,
            name=food.name,
            calories=new_calories,
            meal=meal,
            server_ack_txn_ids=parsed["ack_txn_ids"],
            raw_response_fields=parsed["raw_fields"],
        )

    @staticmethod
    def _nutrients_from_first_serving(food: Food):
        # Some endpoints return nutrition only in the first serving. This
        # fallback lets log_food_from_catalog still work in that case.
        from .food_search import FoodNutrients
        return FoodNutrients()

    # ---- catalog search / barcode -------------------------------

    def barcode_lookup(self, barcode: str, locale: str = "en-US") -> Food:
        """Look up a food by barcode. Returns a decoded Food dataclass."""
        data = self.transport.barcode_lookup(barcode, locale)
        return decode_food(data)

    def search_catalog(
        self,
        query: str,
        *,
        brand: str = "",
        locale: str = "en-US",
        limit: int = 20,
    ) -> list[Food]:
        """Free-text search against LoseIt's full food catalog (not just
        the user's library)."""
        data = self.transport.text_food_search(query, brand=brand, locale=locale, limit=limit)
        return decode_food_search_response(data)
