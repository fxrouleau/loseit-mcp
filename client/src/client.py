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
    MealType,
    build_add_calories_bundle,
    build_create_recipe_bundle,
    build_delete_log_bundle,
    build_delete_recipe_bundle,
    build_log_food_bundle,
    new_uuid16,
    parse_bundle_response,
)
from .db import DEFAULT_CACHE, LogRow, UserDatabase
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
        bundle = build_add_calories_bundle(
            entry,
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
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
        bundle = build_add_calories_bundle(
            entry,
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
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
        quantity: float | None = None,
        day: Optional[dt.date] = None,
    ) -> LoggedEntry:
        """Edit an existing food log entry (one previously created via
        `log_food` or the native app). Reuses the same entry_uuid so the
        server upserts rather than inserting a new row. The food metadata
        is re-looked-up from the local library by uuid.
        """
        from .bundle import build_log_food_bundle

        db = self.database(refresh=False)
        food = db.get_food_by_uuid(food_uuid)
        if food is None:
            raise KeyError(
                f"food {food_uuid.hex()} not in local db; call refresh_database()"
            )
        qty = quantity if quantity is not None else food.last_serving_quantity
        scale = qty / food.last_serving_quantity if food.last_serving_quantity else 1.0
        bundle, _ = build_log_food_bundle(
            food_uuid=food.food_uuid,
            food_name=food.name,
            food_product_name=food.product_name,
            measure_id=food.measure_id,
            measure_singular=food.measure_name,
            measure_plural=food.measure_name_plural,
            serving_quantity=qty,
            serving_base_units=food.last_serving_base_units * scale,
            calories=food.last_serving_calories * scale,
            fat=food.last_serving_fat * scale,
            carbohydrate=food.last_serving_carbohydrate * scale,
            protein=food.last_serving_protein * scale,
            meal=int(meal),
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
            day=day,
            entry_uuid=entry_uuid,
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
        quantity: float | None = None,
        measure_id: int | None = None,
        day: Optional[dt.date] = None,
    ) -> LoggedEntry:
        """Log an existing food from the user's library by uuid.

        Uses the food's last-used serving from ActiveFoods by default.
        Pass `quantity` to scale the serving. Pass `measure_id` (a
        `FoodMeasureId` value) to log the food in a different unit — the
        client will look it up in LoseIt's catalog to find the alternate
        serving and re-scale the nutrition.
        """
        db = self.database(refresh=False)
        food = db.get_food_by_uuid(food_uuid)
        if food is None:
            raise KeyError(f"food {food_uuid.hex()} not in local db; call refresh_database()?")

        if measure_id is not None and measure_id != food.measure_id:
            # User wants a unit the local library doesn't store. Try to
            # find the food in the catalog and pick the matching serving.
            return self._log_food_alt_unit(
                food=food, meal=meal, quantity=quantity,
                measure_id=measure_id, day=day,
            )

        qty = quantity if quantity is not None else food.last_serving_quantity
        base = food.last_serving_base_units
        scale = qty / food.last_serving_quantity if food.last_serving_quantity else 1.0

        bundle, entry_uuid = build_log_food_bundle(
            food_uuid=food.food_uuid,
            food_name=food.name,
            food_product_name=food.product_name,
            measure_id=food.measure_id,
            measure_singular=food.measure_name,
            measure_plural=food.measure_name_plural,
            serving_quantity=qty,
            serving_base_units=base * scale,
            calories=food.last_serving_calories * scale,
            fat=food.last_serving_fat * scale,
            carbohydrate=food.last_serving_carbohydrate * scale,
            protein=food.last_serving_protein * scale,
            meal=int(meal),
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
            day=day,
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

    def _log_food_alt_unit(
        self,
        *,
        food,
        meal: MealType,
        quantity: float | None,
        measure_id: int,
        day: Optional[dt.date],
    ) -> LoggedEntry:
        """Fallback path for log_food when the caller asks for a unit the
        local library doesn't have. Hits the catalog to fetch the alternate
        serving, then routes through log_food_from_catalog.
        """
        # Search by food name; match on uniqueId.
        candidates = self.search_catalog(food.name, limit=20)
        match = next((f for f in candidates if f.unique_id == food.food_uuid), None)
        if match is None:
            # Fall back to name match — catalog uuids can differ from the
            # user's copy when the entry is a custom food or an older
            # snapshot. Pick the first result as best-effort.
            if not candidates:
                raise LookupError(
                    f"catalog has no match for {food.name!r}; cannot log "
                    f"in alternate unit"
                )
            match = candidates[0]
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
            match, meal=meal, quantity=quantity,
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
        flag set."""
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
        quantity: float | None = None,
        serving_index: int = 0,
        day: Optional[dt.date] = None,
    ) -> LoggedEntry:
        """Log a food obtained from barcode lookup or catalog search.

        Picks `food.servings[serving_index]` as the unit. Barcode responses
        typically expose multiple servings (e.g. Skittles returns both
        "27 Pieces" and "40 Grams") — inspect `food.servings` to see what's
        available, then pass the index of the one you want.
        """
        from .bundle import build_log_food_bundle, measure_labels
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
        qty = quantity if quantity is not None else s.size
        # v1 FoodNutrients has no explicit base_units — the nutrition is
        # "per first serving size". Pick that as the unit so the server
        # computes qty × (calories / base) correctly.
        base = s.size or 1.0
        scale = qty / base
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
            serving_quantity=qty,
            serving_base_units=base,
            calories=n.calories * scale,
            fat=n.fat * scale,
            carbohydrate=n.carbohydrates * scale,
            protein=n.protein * scale,
            meal=int(meal),
            user_id=self.user_id,
            sync_token=self._next_sync_token(),
            day=day,
        )
        resp = self.transport.post_transaction_bundle(bundle)
        parsed = parse_bundle_response(resp)
        return LoggedEntry(
            entry_uuid=entry_uuid,
            food_uuid=food.unique_id,
            name=food.name,
            calories=n.calories,
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
