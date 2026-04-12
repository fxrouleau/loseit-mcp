"""Tests for the Food protobuf decoder. Builds synthetic Food messages
in-memory with the Writer and decodes them — no network, no real server.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loseit_client.food_search import (
    MEASURE_NAMES,
    decode_food,
    decode_food_nutrients,
    decode_food_search_response,
    decode_food_serving_size,
)
from loseit_client.pb import Writer


def _nutrient_map(pairs: dict[str, float]) -> bytes:
    """Build a v1 FoodNutrients payload (field 1 = map<string, double>)."""
    w = Writer()
    for k, v in pairs.items():
        entry = Writer().string(1, k).f64(2, v).build()
        w.submsg(1, entry)
    return w.build()


def _serving(measure_id: int, size: float) -> bytes:
    """Build a v1 FoodServingSize payload (enum @1, double @2)."""
    return Writer().varint(1, measure_id).f64(2, size).build()


def _food(
    *,
    uuid: bytes,
    name: str,
    brand: str = "",
    category: str = "",
    locale: str = "en-US",
    nutrients: dict[str, float] | None = None,
    servings: list[tuple[int, float]] | None = None,
) -> bytes:
    w = Writer()
    w.bytes_(1, uuid)
    w.string(2, name)
    if brand:
        w.string(3, brand)
    if category:
        w.string(4, category)
    w.string(5, locale)
    if nutrients is not None:
        w.submsg(6, _nutrient_map(nutrients))
    for measure_id, size in servings or []:
        w.submsg(7, _serving(measure_id, size))
    return w.build()


# ---- nutrients ----

def test_nutrients_canonical_keys_map_to_dataclass():
    data = _nutrient_map(
        {
            "energy": 200,
            "fat": 10,
            "carbohydrate": 20,
            "protein": 15,
            "sugar": 5,
            "fiber": 2,
        }
    )
    n = decode_food_nutrients(data)
    assert n.calories == 200  # aliased from "energy"
    assert n.fat == 10
    assert n.carbohydrates == 20  # aliased from "carbohydrate"
    assert n.protein == 15
    assert n.sugars == 5  # aliased from "sugar"
    assert n.fiber == 2


def test_nutrients_unknown_keys_go_into_extras():
    data = _nutrient_map({"energy": 100, "caffeine": 80, "weight": 40})
    n = decode_food_nutrients(data)
    assert n.calories == 100
    assert n.extras == {"caffeine": 80, "weight": 40}


def test_nutrients_empty():
    n = decode_food_nutrients(b"")
    assert n.calories == 0
    assert n.extras == {}


# ---- serving size ----

def test_serving_size_enum_resolves_to_labels():
    s = decode_food_serving_size(_serving(5, 1.5))  # EACH
    assert s.measure_id == 5
    assert s.measure_singular == "Each"
    assert s.size == 1.5


def test_serving_size_gram():
    s = decode_food_serving_size(_serving(8, 100))
    assert s.measure_id == 8
    assert s.measure_singular == "Gram"
    assert s.measure_plural == "Grams"


def test_serving_size_unknown_enum_falls_back_to_empty_label():
    s = decode_food_serving_size(_serving(9999, 1))
    assert s.measure_id == 9999
    assert s.measure_singular == ""


# ---- full Food ----

def test_food_full_shape():
    data = _food(
        uuid=b"\xab" * 16,
        name="Skittles 27 Pieces",
        category="Candy",
        nutrients={"energy": 160, "fat": 1.5, "sugar": 30, "weight": 40},
        servings=[(31, 27), (8, 40)],  # SERVING, GRAM
    )
    f = decode_food(data)
    assert f.unique_id == b"\xab" * 16
    assert f.name == "Skittles 27 Pieces"
    assert f.category == "Candy"
    assert f.language_tag == "en-US"

    assert f.nutrients is not None
    assert f.nutrients.calories == 160
    assert f.nutrients.extras.get("weight") == 40

    assert len(f.servings) == 2
    assert f.servings[0].measure_singular == "Serving"
    assert f.servings[0].size == 27
    assert f.servings[1].measure_singular == "Gram"
    assert f.servings[1].size == 40


def test_food_search_response_wrapper():
    f1 = _food(uuid=b"\x01" * 16, name="Banana", servings=[(5, 1)])
    f2 = _food(uuid=b"\x02" * 16, name="Apple", servings=[(5, 1)])
    # FoodSearchResponse has one repeated field 1 of Food
    resp = Writer().submsg(1, f1).submsg(1, f2).build()
    foods = decode_food_search_response(resp)
    assert len(foods) == 2
    names = {f.name for f in foods}
    assert names == {"Banana", "Apple"}


def test_measure_names_table_covers_common_units():
    """food_search.MEASURE_NAMES is independent of bundle.MEASURE_LABELS
    but should agree on the common ones. Smoke check a few."""
    assert MEASURE_NAMES[5] == ("Each", "Each")
    assert MEASURE_NAMES[8] == ("Gram", "Grams")
    assert MEASURE_NAMES[31] == ("Serving", "Servings")
