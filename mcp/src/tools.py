"""MCP tool definitions — thin wrappers over LoseItClient methods.

Each function is registered as an MCP tool with a typed signature that
Claude can call directly. Return values are plain dicts/lists (JSON
serialisable) so the MCP SDK can marshal them.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from loseit_client import FoodMeasureId, LoseItClient, MealType


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


def register(mcp: Any, client: LoseItClient) -> None:
    """Register all tools on a FastMCP instance. `mcp` is typed as Any to
    avoid importing the MCP SDK at module import time.
    """

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
    def search_foods(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search the user's personal food library (ActiveFoods).

        These are foods the user has logged at least once before. Prefer
        this over `search_catalog` when the user talks about familiar
        items — the match will re-use the exact macros they know.
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
        """Search LoseIt's full global food catalog (millions of items)."""
        foods = client.search_catalog(query, limit=limit)
        out = []
        for f in foods:
            n = f.nutrients
            out.append(
                {
                    "food_uuid": f.unique_id.hex(),
                    "name": f.name,
                    "brand": f.brand_name,
                    "category": f.category,
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
                        for s in f.servings
                    ],
                }
            )
        return out

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
        """Look up a food by UPC/EAN barcode. Returns full Food details."""
        f = client.barcode_lookup(barcode)
        n = f.nutrients
        return {
            "food_uuid": f.unique_id.hex(),
            "name": f.name,
            "brand": f.brand_name,
            "category": f.category,
            "calories": n.calories if n else None,
            "fat_g": n.fat if n else None,
            "carbohydrate_g": n.carbohydrates if n else None,
            "protein_g": n.protein if n else None,
            "servings": [
                {"size": s.size, "measure": s.measure_singular, "measure_id": s.measure_id}
                for s in f.servings
            ],
        }

    @mcp.tool()
    def log_food(
        food_uuid: str,
        meal: str,
        quantity: Optional[float] = None,
        measure: Optional[str] = None,
        date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Log an existing food from the user's library.

        Args:
            food_uuid: hex uuid of a food returned by `search_foods`.
            meal: one of "breakfast", "lunch", "dinner", "snacks".
            quantity: amount to log (in the food's default unit or the
                `measure` unit if given). Defaults to the food's last-used
                serving size.
            measure: optional unit override, e.g. "GRAM", "CUP". See
                `list_units` for valid values.
            date: ISO date (YYYY-MM-DD). Defaults to today.
        """
        r = client.log_food(
            food_uuid=bytes.fromhex(food_uuid),
            meal=_meal_from_str(meal),
            quantity=quantity,
            measure_id=_measure_from_str(measure),
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
        """Log a one-off calorie entry with macros. Best for complex meals
        where no single catalog item fits (homemade dishes, restaurant plates).
        Give a descriptive `name` — it's what shows in the log."""
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
    def log_food_from_barcode(
        barcode: str,
        meal: str,
        quantity: Optional[float] = None,
        serving_index: int = 0,
        date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Look up a barcode and log the result in one call."""
        food = client.barcode_lookup(barcode)
        from loseit_client.client import LoggedEntry  # noqa: F401 — type only
        r = client.log_food_from_catalog(
            food,
            meal=_meal_from_str(meal),
            quantity=quantity,
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
    def edit_log_entry(
        entry_uuid: str,
        food_uuid: str,
        meal: str,
        quantity: Optional[float] = None,
        date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Change the quantity of an existing log entry (reuses its uuid)."""
        r = client.edit_food_entry(
            entry_uuid=bytes.fromhex(entry_uuid),
            food_uuid=bytes.fromhex(food_uuid),
            meal=_meal_from_str(meal),
            quantity=quantity,
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
        by `get_day_log` — tombstone mechanics require the full record."""
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
