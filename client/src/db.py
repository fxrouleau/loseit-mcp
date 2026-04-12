"""Read-only view over the user's SQLite database snapshot.

The snapshot comes from `POST /user/database?newschema` and is a full dump
of the user's state: every food they've ever used (ActiveFoods), every log
entry (FoodLogEntries), custom recipes, goal history, etc. Querying it is
dramatically faster than round-tripping to the server for every read.

Typical lifecycle:
  db = UserDatabase.download(transport)
  db.get_day_log(date.today())
  db.search_foods("apple", limit=10)
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .bundle import LOSEIT_EPOCH, MealType


CACHE_DIR = Path.home() / ".loseit_client"
DEFAULT_CACHE = CACHE_DIR / "user_db.sqlite"


def day_to_date(day: int) -> dt.date:
    return LOSEIT_EPOCH + dt.timedelta(days=day)


def date_to_day(d: dt.date) -> int:
    return (d - LOSEIT_EPOCH).days


@dataclass
class LogRow:
    entry_uuid: bytes
    food_uuid: bytes
    food_name: str
    meal: MealType
    calories: float
    fat: float
    carbohydrate: float
    protein: float
    measure_id: int
    measure_name: str
    measure_name_plural: str
    quantity: float
    date_day: int

    @property
    def date(self) -> dt.date:
        return day_to_date(self.date_day)


@dataclass
class FoodRow:
    food_uuid: bytes
    name: str
    product_name: str
    measure_id: int
    measure_name: str
    measure_name_plural: str
    last_serving_quantity: float
    last_serving_base_units: float
    last_serving_calories: float
    last_serving_fat: float
    last_serving_carbohydrate: float
    last_serving_protein: float


@dataclass
class RecipeRow:
    recipe_uuid: bytes
    name: str
    brand: Optional[str]
    notes: Optional[str]


class UserDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._con = sqlite3.connect(path)
        self._con.row_factory = sqlite3.Row

    @classmethod
    def download(cls, transport, *, cache: Path = DEFAULT_CACHE) -> "UserDatabase":
        cache.parent.mkdir(parents=True, exist_ok=True)
        data = transport.get_user_database()
        cache.write_bytes(data)
        return cls(cache)

    @classmethod
    def from_cache(cls, *, cache: Path = DEFAULT_CACHE) -> "UserDatabase":
        if not cache.exists():
            raise FileNotFoundError(
                f"No cached user database at {cache}. Call UserDatabase.download(transport) first."
            )
        return cls(cache)

    # ---- log queries -----------------------------------------------

    def get_day_log(self, day: dt.date) -> list[LogRow]:
        day_num = date_to_day(day)
        rows = self._con.execute(
            """
            SELECT
              fle.UniqueId      AS entry_uuid,
              fle.FoodUniqueId  AS food_uuid,
              af.Name           AS food_name,
              fle.MealType      AS meal,
              fle.Calories      AS calories,
              COALESCE(fle.Fat, 0)           AS fat,
              COALESCE(fle.Carbohydrates, 0) AS carbohydrate,
              COALESCE(fle.Protein, 0)       AS protein,
              fle.MeasureId     AS measure_id,
              fle.MeasureName   AS measure_name,
              fle.MeasureNamePlural AS measure_name_plural,
              fle.Quantity      AS quantity,
              fle.Date          AS date_day
            FROM FoodLogEntries fle
            LEFT JOIN ActiveFoods af ON af.UniqueId = fle.FoodUniqueId
            WHERE fle.Date = ? AND fle.Deleted = 0
            ORDER BY fle.MealType, fle.EntryOrder
            """,
            (day_num,),
        ).fetchall()
        return [
            LogRow(
                entry_uuid=r["entry_uuid"],
                food_uuid=r["food_uuid"],
                food_name=r["food_name"] or self._resolve_name(r["food_uuid"]),
                meal=MealType(r["meal"]),
                calories=r["calories"],
                fat=r["fat"],
                carbohydrate=r["carbohydrate"],
                protein=r["protein"],
                measure_id=r["measure_id"],
                measure_name=r["measure_name"],
                measure_name_plural=r["measure_name_plural"] or r["measure_name"],
                quantity=r["quantity"],
                date_day=r["date_day"],
            )
            for r in rows
        ]

    def _resolve_name(self, food_uuid: bytes) -> str:
        """Fallback when a log entry points at a food not in ActiveFoods.
        Checks EntityValues for FoodLogOverrideName, then gives up."""
        cur = self._con.execute(
            "SELECT Value FROM EntityValues WHERE EntityId = ? AND Name = 'FoodLogOverrideName' LIMIT 1",
            (food_uuid,),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        return "(unknown)"

    # ---- food library (ActiveFoods) --------------------------------

    def search_foods(self, query: str, *, limit: int = 20) -> list[FoodRow]:
        """Case-insensitive substring search over the user's food library."""
        pattern = f"%{query}%"
        rows = self._con.execute(
            """
            SELECT
              UniqueId AS food_uuid,
              Name, ProductName,
              MeasureId, MeasureName, MeasureNamePlural,
              LastServingQuantity, LastServingBaseUnits, LastServingCalories,
              COALESCE(LastServingFat, 0)         AS fat,
              COALESCE(LastServingCarbohydrates,0) AS carbohydrate,
              COALESCE(LastServingProtein, 0)     AS protein
            FROM ActiveFoods
            WHERE Visible = 1 AND Name LIKE ? COLLATE NOCASE
            ORDER BY LastUsed DESC, TotalUsages DESC
            LIMIT ?
            """,
            (pattern, limit),
        ).fetchall()
        return [
            FoodRow(
                food_uuid=r["food_uuid"],
                name=r["Name"],
                product_name=r["ProductName"] or r["Name"],
                measure_id=r["MeasureId"],
                measure_name=r["MeasureName"],
                measure_name_plural=r["MeasureNamePlural"] or r["MeasureName"],
                last_serving_quantity=r["LastServingQuantity"],
                last_serving_base_units=r["LastServingBaseUnits"],
                last_serving_calories=r["LastServingCalories"],
                last_serving_fat=r["fat"],
                last_serving_carbohydrate=r["carbohydrate"],
                last_serving_protein=r["protein"],
            )
            for r in rows
        ]

    def get_food_by_uuid(self, food_uuid: bytes) -> Optional[FoodRow]:
        rows = self._con.execute(
            """
            SELECT
              UniqueId AS food_uuid,
              Name, ProductName, MeasureId, MeasureName, MeasureNamePlural,
              LastServingQuantity, LastServingBaseUnits, LastServingCalories,
              COALESCE(LastServingFat, 0)         AS fat,
              COALESCE(LastServingCarbohydrates,0) AS carbohydrate,
              COALESCE(LastServingProtein, 0)     AS protein
            FROM ActiveFoods WHERE UniqueId = ?
            """,
            (food_uuid,),
        ).fetchall()
        if not rows:
            return None
        r = rows[0]
        return FoodRow(
            food_uuid=r["food_uuid"],
            name=r["Name"],
            product_name=r["ProductName"] or r["Name"],
            measure_id=r["MeasureId"],
            measure_name=r["MeasureName"],
            measure_name_plural=r["MeasureNamePlural"] or r["MeasureName"],
            last_serving_quantity=r["LastServingQuantity"],
            last_serving_base_units=r["LastServingBaseUnits"],
            last_serving_calories=r["LastServingCalories"],
            last_serving_fat=r["fat"],
            last_serving_carbohydrate=r["carbohydrate"],
            last_serving_protein=r["protein"],
        )

    # ---- recipes ---------------------------------------------------

    def search_recipes(self, query: str, *, limit: int = 20) -> list[RecipeRow]:
        rows = self._con.execute(
            """
            SELECT UniqueId, Name, Brand, Notes FROM Recipes
            WHERE Deleted = 0 AND Name LIKE ? COLLATE NOCASE
            ORDER BY Name
            LIMIT ?
            """,
            (f"%{query}%", limit),
        ).fetchall()
        return [
            RecipeRow(
                recipe_uuid=r["UniqueId"],
                name=r["Name"],
                brand=r["Brand"],
                notes=r["Notes"],
            )
            for r in rows
        ]
