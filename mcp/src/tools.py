"""MCP tool definitions — thin wrappers over LoseItClient methods.

Each function is registered as an MCP tool with a typed signature that
Claude can call directly. Return values are plain dicts/lists (JSON
serialisable) so the MCP SDK can marshal them.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from loseit_client import Food, FoodMeasureId, LoseItClient, MealType


# ---- in-process catalog cache ---------------------------------------
#
# `search_catalog` and `barcode_lookup` return Food objects from
# LoseIt's global food-search service. Those Foods are NOT in the
# user's local SQLite snapshot, so a naive `log_food(catalog_uuid)`
# would fail the local-library lookup. We stash every catalog Food
# we hand back to the LLM, keyed by uuid; `log_food` consults this
# cache when the uuid isn't in the snapshot, so the LLM can pass any
# uuid it received from any search tool without caring which.
#
# Single-process uvicorn means this dict is server-wide. For one
# connected user that's fine. If we ever scale to multiple workers,
# replace with a shared store.
_catalog_cache: dict[bytes, Food] = {}


def _cache_food(food: Food) -> None:
    _catalog_cache[food.unique_id] = food


def _meal_from_str(s: str) -> MealType:
    try:
        return MealType[s.upper()]
    except KeyError as exc:
        raise ValueError(
            f"invalid meal {s!r}; must be one of breakfast, lunch, dinner, snacks"
        ) from exc


def _measure_from_str(s: str | None) -> int | None:
    if s is None:
        return None
    try:
        return int(FoodMeasureId[s.upper()])
    except KeyError as exc:
        raise ValueError(
            f"invalid measure {s!r}; see list_units() for valid values"
        ) from exc


def _date_from_str(s: str | None) -> dt.date | None:
    if s is None:
        return None
    return dt.date.fromisoformat(s)


def _serialize_food(food: Food) -> dict[str, Any]:
    n = food.nutrients
    return {
        "food_uuid": food.unique_id.hex(),
        "name": food.name,
        "brand": food.brand_name,
        "category": food.category,
        "calories": n.calories if n else None,
        "fat_g": n.fat if n else None,
        "carbohydrate_g": n.carbohydrates if n else None,
        "protein_g": n.protein if n else None,
        "servings": [
            {
                "size": s.size,
                "measure": s.measure_singular,
                "measure_id": s.measure_id,
            }
            for s in food.servings
        ],
    }


def register(mcp: Any, client: LoseItClient) -> None:
    """Register all tools on a FastMCP instance. `mcp` is typed as Any to
    avoid importing the MCP SDK at module import time.
    """

    # ---- routing guidance for the LLM -----------------------------
    # The "right" tool depends on how much the food's macros vary
    # across instances of the same name:
    #
    #   * Low variance: raw whole foods (apple, banana, carrot, egg,
    #     plain chicken breast, cooked rice) and fully refined branded
    #     products (Goldfish crackers, a specific protein bar, a
    #     specific cereal). 1 of these means the same thing every
    #     time. → search_foods → log_food, or search_catalog →
    #     log_food, or barcode_lookup → log_food.
    #
    #   * High variance: cooked / mixed / restaurant / homemade meals
    #     where the same name covers wildly different calorie counts
    #     (bun bo hue, pho, chicken pot pie, a slice of cake, a bowl
    #     of ice cream, "pasta with sauce", any restaurant dish).
    #     Forcing a catalog match would mis-represent what the user
    #     actually ate. → log_calories with a descriptive name and
    #     your best macro estimate.
    #
    # When in doubt, prefer log_calories. The descriptive name is the
    # entry's title in the user's log and matters more than catalog
    # accuracy for non-trivial meals.

    @mcp.tool()
    def list_units() -> list[dict[str, Any]]:
        """Return every supported food measure (unit) the server understands.

        Use the `name` field when calling tools that take a `measure`
        argument (e.g. "GRAM", "CUP", "OUNCE"). 48 units total.
        """
        return [
            {"name": m.name, "id": int(m), "label_singular": m.name.title(), "value": int(m)}
            for m in FoodMeasureId
        ]

    @mcp.tool()
    def get_day_log(date: Optional[str] = None) -> list[dict[str, Any]]:
        """Return every non-deleted food log entry for a given day.

        Args:
            date: ISO date (YYYY-MM-DD). Defaults to today.
        """
        rows = client.get_day_log(_date_from_str(date))
        return [
            {
                "entry_uuid": r.entry_uuid.hex(),
                "food_uuid": r.food_uuid.hex(),
                "food_name": r.food_name,
                "meal": r.meal.name.lower(),
                "calories": r.calories,
                "fat_g": r.fat,
                "carbohydrate_g": r.carbohydrate,
                "protein_g": r.protein,
                "quantity": r.quantity,
                "measure": r.measure_name,
                "date": r.date.isoformat(),
            }
            for r in rows
        ]

    @mcp.tool()
    def refresh_database() -> dict[str, Any]:
        """Re-download the user database snapshot from LoseIt.

        Use this when search_foods or get_day_log returns stale data
        — for example after the user has made changes from another
        device since the server started, or after logging a brand-new
        food via log_food and wanting to immediately see it appear in
        search_foods.
        """
        client.refresh_database()
        return {"ok": True, "message": "user database snapshot refreshed"}

    @mcp.tool()
    def search_foods(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search the user's personal food library (foods they've already
        logged at least once). Try this BEFORE search_catalog — re-using
        a familiar entry preserves the exact macros the user expects.
        """
        rows = client.search_foods(query, limit=limit)
        return [
            {
                "food_uuid": r.food_uuid.hex(),
                "name": r.name,
                "measure": r.measure_name,
                "calories_per_serving": r.last_serving_calories,
                "serving_quantity": r.last_serving_quantity,
                "fat_g": r.last_serving_fat,
                "carbohydrate_g": r.last_serving_carbohydrate,
                "protein_g": r.last_serving_protein,
            }
            for r in rows
        ]

    @mcp.tool()
    def search_catalog(query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search LoseIt's full global food catalog (millions of items).

        Only use this for unambiguous single foods — a specific fruit,
        a packaged item, a clearly named ingredient. For anything else
        (mixed dishes, homemade meals, restaurant entrees), prefer
        log_calories with a descriptive name and your own macro
        estimate. Catalog entries you return from here can be fed
        straight into log_food by their food_uuid.
        """
        foods = client.search_catalog(query, limit=limit)
        for f in foods:
            _cache_food(f)
        return [_serialize_food(f) for f in foods]

    @mcp.tool()
    def search_recipes(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search the user's custom recipes."""
        rows = client.search_recipes(query, limit=limit)
        return [
            {"recipe_uuid": r.recipe_uuid.hex(), "name": r.name, "brand": r.brand, "notes": r.notes}
            for r in rows
        ]

    @mcp.tool()
    def barcode_lookup(barcode: str) -> dict[str, Any]:
        """Look up a food by UPC/EAN barcode. Returns the same Food shape
        as search_catalog; the food_uuid can be passed straight into
        log_food."""
        f = client.barcode_lookup(barcode)
        _cache_food(f)
        return _serialize_food(f)

    @mcp.tool()
    def log_food(
        food_uuid: str,
        meal: str,
        servings: float = 1.0,
        serving_index: int = 0,
        date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Log a food entry for a single identifiable food.

        **Use this for raw / refined items only**: apple, banana,
        carrot, egg, chicken breast, plain rice, a specific branded
        packaged product. For variable / cooked / restaurant /
        homemade meals (bun bo hue, pho, ice cream, cake, pasta with
        sauce, etc.) use `log_calories` instead — the catalog will
        mis-represent what the user actually ate.

        Accepts a `food_uuid` from any of:
          - search_foods       (user's personal library)
          - search_catalog     (LoseIt's global catalog)
          - barcode_lookup     (UPC scan / barcode lookup)

        The tool figures out which source the uuid came from
        automatically — you don't need to track it.

        ## How to specify "how much"

        Use **`servings`**: the number of standard servings of the
        food, exactly as the user describes it. Matches the LoseIt
        app's picker behaviour:

        * "log 2 carrots"      → servings=2 (= 2 × the food's serving)
        * "log 1 apple"        → servings=1   (or omit; default is 1)
        * "log half a banana"  → servings=0.5

        Servings are always multiplicative on the food's stored
        serving size. You DO NOT need to think about grams or units.
        For a carrot whose catalog entry has serving "61 Grams",
        `servings=2` correctly logs 122 grams (50 cal). For an apple
        stored as "1 Each", `servings=2` logs 2 each.

        ## When the user gives a raw weight or volume

        If the user says "200 grams of carrot" and the food's serving
        is "61 Grams", compute it yourself:
            servings = 200 / 61 ≈ 3.28
        There is intentionally no `quantity_in_grams` parameter — it's
        the most common source of bugs.

        ## When the user wants a different unit than what's stored

        Call `search_catalog` for the food, look at the `servings`
        list in each result, pick the one that has the unit you want,
        and pass that food_uuid + serving_index. Don't try to "convert"
        a local-library food into a different unit through this tool —
        you'll either get an error or the wrong food.

        Args:
            food_uuid: hex uuid from a search/lookup response.
            meal: "breakfast" | "lunch" | "dinner" | "snacks".
            servings: number of servings (multiplier on serving size).
                Default 1.0.
            serving_index: which entry of the food's `servings` list
                to use, for catalog/barcode foods that expose multiple
                units (e.g. "27 Pieces" + "40 Grams"). Default 0.
                Ignored for personal-library foods.
            date: ISO date (YYYY-MM-DD). Defaults to today.

        Returns the new log entry's metadata.
        """
        fu = bytes.fromhex(food_uuid)

        # Try the user's personal library first — it has the exact
        # macros they're used to seeing.
        db = client.database(refresh=False)
        local = db.get_food_by_uuid(fu)
        if local is not None:
            r = client.log_food(
                food_uuid=fu,
                meal=_meal_from_str(meal),
                servings=servings,
                day=_date_from_str(date),
            )
        else:
            cached = _catalog_cache.get(fu)
            if cached is None:
                raise KeyError(
                    f"food {food_uuid} is not in your library and not in "
                    f"the catalog cache. Call search_foods, search_catalog, "
                    f"or barcode_lookup first to make it known to the server."
                )
            r = client.log_food_from_catalog(
                cached,
                meal=_meal_from_str(meal),
                servings=servings,
                serving_index=serving_index,
                day=_date_from_str(date),
            )
        return {
            "entry_uuid": r.entry_uuid.hex(),
            "food_uuid": r.food_uuid.hex(),
            "name": r.name,
            "calories": r.calories,
            "meal": r.meal.name.lower(),
        }

    @mcp.tool()
    def log_calories(
        name: str,
        calories: float,
        meal: str,
        fat_g: float = 0.0,
        carbohydrate_g: float = 0.0,
        protein_g: float = 0.0,
        date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Log a free-form calorie + macros entry under a descriptive name.

        ## When to use this

        Anything where the same name covers a wide range of actual
        macros: cooked dishes, restaurant meals, homemade recipes,
        mixed plates, ice cream of a non-specific brand, "pasta with
        sauce", "pho", "bun bo hue", "chicken pot pie", "a slice of
        cake", "leftovers". The catalog has entries for these but
        forcing a match would mis-represent what the user actually
        ate. Estimate macros yourself and write a descriptive name.

        Reach for `log_food` only when the food is **low-variance**:
        a raw whole food (apple, banana, carrot, egg, plain rice) or a
        fully refined branded product (Goldfish, a specific protein
        bar, a specific cereal). When in doubt, use this tool.

        The `name` becomes the title of the entry in the LoseIt log,
        so make it descriptive — "Bun bo hue large bowl" beats
        "soup", "homemade chicken tikka masala 1 plate" beats
        "chicken curry". Future-you needs to read the log and
        remember what it was.
        """
        r = client.log_calories(
            name=name,
            calories=calories,
            fat=fat_g,
            carbohydrate=carbohydrate_g,
            protein=protein_g,
            meal=_meal_from_str(meal),
            day=_date_from_str(date),
        )
        return {
            "entry_uuid": r.entry_uuid.hex(),
            "food_uuid": r.food_uuid.hex(),
            "name": r.name,
            "calories": r.calories,
            "meal": r.meal.name.lower(),
        }

    @mcp.tool()
    def edit_log_entry(
        entry_uuid: str,
        food_uuid: str,
        meal: str,
        servings: float = 1.0,
        date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Change the amount of an existing food log entry (reuses its
        uuid). Pass the entry_uuid + food_uuid you got from get_day_log.

        `servings` is the new total amount in the food's standard
        servings. See `log_food` for the full explanation. Default 1.
        """
        r = client.edit_food_entry(
            entry_uuid=bytes.fromhex(entry_uuid),
            food_uuid=bytes.fromhex(food_uuid),
            meal=_meal_from_str(meal),
            servings=servings,
            day=_date_from_str(date),
        )
        return {
            "entry_uuid": r.entry_uuid.hex(),
            "food_uuid": r.food_uuid.hex(),
            "calories": r.calories,
        }

    @mcp.tool()
    def delete_log_entry(
        entry_uuid: str,
        food_uuid: str,
        food_name: str,
        meal: str,
        calories: float,
        fat_g: float = 0.0,
        carbohydrate_g: float = 0.0,
        protein_g: float = 0.0,
        date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Delete a food log entry. Supply the full entry info returned
        by get_day_log — tombstone mechanics require the full record.
        """
        r = client.delete_log_entry(
            entry_uuid=bytes.fromhex(entry_uuid),
            food_uuid=bytes.fromhex(food_uuid),
            food_name=food_name,
            meal=_meal_from_str(meal),
            calories=calories,
            fat=fat_g,
            carbohydrate=carbohydrate_g,
            protein=protein_g,
            day=_date_from_str(date),
        )
        return {"ack_txn_ids": r["ack_txn_ids"]}

    @mcp.tool()
    def create_recipe(
        name: str,
        ingredients: list[dict[str, Any]],
        total_servings: float = 1.0,
    ) -> dict[str, str]:
        """Create a custom recipe.

        Args:
            name: recipe name.
            ingredients: list of `{"food_uuid": hex, "quantity": float}`
                — each refers to a food in the user's library.
            total_servings: number of servings the recipe yields.
        """
        specs = [
            (bytes.fromhex(ing["food_uuid"]), float(ing["quantity"]))
            for ing in ingredients
        ]
        uuid = client.create_recipe(name, specs, total_servings=total_servings)
        return {"recipe_uuid": uuid.hex()}

    @mcp.tool()
    def delete_recipe(recipe_uuid: str, recipe_name: str) -> dict[str, Any]:
        """Delete a custom recipe by uuid (tombstone)."""
        r = client.delete_recipe(
            recipe_uuid=bytes.fromhex(recipe_uuid), recipe_name=recipe_name
        )
        return {"ack_txn_ids": r["ack_txn_ids"]}
