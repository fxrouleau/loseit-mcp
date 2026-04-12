"""Decoder for `com.fitnow.foundation.food.v1.Food` and its friends.

These are the protobuf messages returned by the food-search microservice
(`food-search.prod.fitnowinc.com`) for both barcode lookup and free-text
search. Field numbers extracted from Food.java in the jadx decompile.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from .pb import f64_from_uint, read_message


@dataclass
class FoodServingSize:
    description: str = ""
    size: float = 0.0
    measure_id: int = 0
    measure_singular: str = ""
    measure_plural: str = ""


@dataclass
class FoodNutrients:
    base_units: float = 0.0
    calories: float = 0.0
    fat: float = 0.0
    saturated_fat: float = 0.0
    cholesterol: float = 0.0
    sodium: float = 0.0
    carbohydrates: float = 0.0
    fiber: float = 0.0
    sugars: float = 0.0
    protein: float = 0.0
    extras: dict[str, float] = field(default_factory=dict)


@dataclass
class Food:
    """Decoded `com.fitnow.foundation.food.v1.Food`."""
    unique_id: bytes = b""
    name: str = ""
    brand_name: str = ""
    category: str = ""
    language_tag: str = ""
    nutrients: Optional[FoodNutrients] = None
    servings: list[FoodServingSize] = field(default_factory=list)
    curation_level: int = 0
    product_type: int = 0


def _u_to_f(u: int) -> float:
    return f64_from_uint(u)


def decode_food_nutrients(data: bytes) -> FoodNutrients:
    """`com.fitnow.foundation.food.v1.FoodNutrients` — a proto3
    `map<string, double>` where each entry is {key: field 1, value: field 2}.
    Different shape from `UserDatabaseProtocol.FoodNutrients`.
    """
    m = read_message(data)
    n = FoodNutrients()
    # Every key/value lives in the repeated field 1
    raw: dict[str, float] = {}
    for entry in m.get(1, []):
        e = read_message(entry)
        key_bytes = e.get(1, [b""])[0]
        val_raw = e.get(2, [0])[0]
        key = key_bytes.decode("utf-8", "replace") if isinstance(key_bytes, bytes) else str(key_bytes)
        val = _u_to_f(val_raw) if isinstance(val_raw, int) else float(val_raw)
        raw[key] = val
    alias = {
        "energy": "calories",
        "carbohydrate": "carbohydrates",
        "sugar": "sugars",
        "base_units": "base_units",
    }
    mapped_keys = {"calories", "fat", "saturated_fat", "cholesterol", "sodium",
                   "carbohydrates", "fiber", "sugars", "protein", "base_units"}
    for k, v in raw.items():
        target = alias.get(k, k)
        if target in mapped_keys:
            setattr(n, target, v)
        else:
            n.extras[k] = v
    return n


MEASURE_NAMES: dict[int, tuple[str, str]] = {
    0: ("", ""),
    1: ("Teaspoon", "Teaspoons"),
    2: ("Tablespoon", "Tablespoons"),
    3: ("Cup", "Cups"),
    4: ("Piece", "Pieces"),
    5: ("Each", "Each"),
    6: ("Ounce", "Ounces"),
    7: ("Pound", "Pounds"),
    8: ("Gram", "Grams"),
    9: ("Kilogram", "Kilograms"),
    10: ("Fluid Ounce", "Fluid Ounces"),
    11: ("Milliliter", "Milliliters"),
    12: ("Liter", "Liters"),
    13: ("Gallon", "Gallons"),
    14: ("Pint", "Pints"),
    15: ("Quart", "Quarts"),
    16: ("Milligram", "Milligrams"),
    17: ("Microgram", "Micrograms"),
    18: ("Intake", "Intakes"),
    20: ("Bottle", "Bottles"),
    21: ("Box", "Boxes"),
    22: ("Can", "Cans"),
    24: ("Cube", "Cubes"),
    25: ("Jar", "Jars"),
    26: ("Stick", "Sticks"),
    27: ("Tablet", "Tablets"),
    30: ("Slice", "Slices"),
    31: ("Serving", "Servings"),
    37: ("Scoop", "Scoops"),
}


def decode_food_serving_size(data: bytes) -> FoodServingSize:
    """`com.fitnow.foundation.food.v1.FoodServingSize`: just {measureType
    enum @1, size double @2}. No description / unit strings — those come
    from the enum table above.
    """
    m = read_message(data)
    s = FoodServingSize()
    if 1 in m: s.measure_id = m[1][0]
    if 2 in m: s.size = _u_to_f(m[2][0])
    singular, plural = MEASURE_NAMES.get(s.measure_id, ("", ""))
    s.measure_singular = singular
    s.measure_plural = plural
    s.description = f"{int(s.size) if s.size == int(s.size) else s.size} {plural or singular}".strip()
    return s


def decode_food(data: bytes) -> Food:
    m = read_message(data)
    f = Food()
    if 1 in m: f.unique_id = m[1][0]
    if 2 in m and isinstance(m[2][0], bytes):
        f.name = m[2][0].decode("utf-8", "replace")
    if 3 in m and isinstance(m[3][0], bytes):
        f.brand_name = m[3][0].decode("utf-8", "replace")
    if 4 in m and isinstance(m[4][0], bytes):
        f.category = m[4][0].decode("utf-8", "replace")
    if 5 in m and isinstance(m[5][0], bytes):
        f.language_tag = m[5][0].decode("utf-8", "replace")
    if 6 in m:
        f.nutrients = decode_food_nutrients(m[6][0])
    for s_bytes in m.get(7, []):
        f.servings.append(decode_food_serving_size(s_bytes))
    if 8 in m: f.curation_level = m[8][0]
    if 9 in m: f.product_type = m[9][0]
    return f


def decode_food_search_response(data: bytes) -> list[Food]:
    """FoodSearchResponse has one field: repeated Food foods = 1."""
    m = read_message(data)
    return [decode_food(entry) for entry in m.get(1, [])]
