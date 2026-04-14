"""Pure-function tests for bundle building: shape, date math, tombstones,
recipe creation. No network, no mocks."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loseit_client.bundle import (
    LOSEIT_EPOCH,
    CaloriesEntry,
    FoodMeasureId,
    IngredientSpec,
    MealType,
    MEASURE_LABELS,
    build_add_calories_bundle,
    build_create_recipe_bundle,
    build_delete_log_bundle,
    build_delete_recipe_bundle,
    build_log_food_bundle,
    days_since_loseit_epoch,
    measure_labels,
)
from loseit_client.pb import f64_from_uint, read_message


USER_ID = 34641935
SYNC_TOKEN = 1_775_972_751_000


# ---- date math ----

def test_loseit_epoch_is_2000_12_31():
    assert LOSEIT_EPOCH == dt.date(2000, 12, 31)


def test_known_day_2026_04_12_is_9233():
    """Captured bundles on 2026-04-12 all showed day_day = 9233."""
    assert days_since_loseit_epoch(dt.date(2026, 4, 12)) == 9233


def test_day_zero_is_epoch_itself():
    assert days_since_loseit_epoch(LOSEIT_EPOCH) == 0
    assert days_since_loseit_epoch(LOSEIT_EPOCH + dt.timedelta(days=1)) == 1


# ---- measure labels ----

def test_all_48_measures_have_labels():
    assert len(FoodMeasureId) == 48
    for m in FoodMeasureId:
        assert m in MEASURE_LABELS, f"missing label for {m.name}"


def test_measure_labels_helper():
    assert measure_labels(FoodMeasureId.EACH) == ("Each", "Each")
    assert measure_labels(FoodMeasureId.GRAM) == ("Gram", "Grams")
    assert measure_labels(FoodMeasureId.SERVING) == ("Serving", "Servings")
    assert measure_labels(999) == ("", "")  # unknown id


# ---- Add Calories bundle shape ----

def test_add_calories_bundle_shape():
    entry = CaloriesEntry(
        name="mcp-test", calories=150, fat=5, carbohydrate=10, protein=8,
        meal=MealType.SNACKS, day=dt.date(2026, 4, 12),
    )
    data = build_add_calories_bundle(entry, user_id=USER_ID, sync_token=SYNC_TOKEN)

    top = read_message(data)
    # envelope
    assert top[2][0] == SYNC_TOKEN
    assert top[4][0] == USER_ID

    # single transaction
    assert len(top[1]) == 1
    txn = read_message(top[1][0])
    assert 2 in txn, "expected active_foods"
    assert 7 in txn, "expected food_log_entries"
    assert 23 in txn, "expected entity_values (override name)"

    # FoodLogEntry context
    fle = read_message(txn[7][0])
    ctx = read_message(fle[2][0])
    assert ctx[2][0] == 9233                           # date
    assert ctx[3][0] == int(MealType.SNACKS)           # meal enum
    assert ctx[6][0] == 0                              # not deleted
    assert len(ctx[5][0]) == 16                        # uuid is 16 bytes


def test_add_calories_is_not_deleted():
    entry = CaloriesEntry(name="x", calories=50, meal=MealType.LUNCH)
    data = build_add_calories_bundle(entry, user_id=USER_ID, sync_token=SYNC_TOKEN)
    txn = read_message(read_message(data)[1][0])
    ctx = read_message(read_message(txn[7][0])[2][0])
    assert ctx[6][0] == 0                              # deleted flag = false


def test_meal_enum_values_land_on_wire():
    """Meal enum must appear at field 3 of FoodLogEntryContext."""
    for meal in MealType:
        entry = CaloriesEntry(name="x", calories=1, meal=meal)
        data = build_add_calories_bundle(entry, user_id=USER_ID, sync_token=0)
        txn = read_message(read_message(data)[1][0])
        ctx = read_message(read_message(txn[7][0])[2][0])
        assert ctx[3][0] == int(meal), f"meal {meal.name} not on wire"


def test_log_food_bundle_keeps_size_and_base_units_consistent():
    """Regression: a previous bug had the catalog log path scaling
    calories without scaling base_units, so the wire bundle had
    serving_size != base_units and the server interpreted the request
    as a tiny gram amount (e.g. logging "2 grams" instead of "2 carrots").
    The two fields must always agree."""
    from loseit_client.pb import f64_from_uint

    data, _ = build_log_food_bundle(
        food_uuid=b"\x33" * 16,
        food_name="Carrot",
        food_product_name="Carrot",
        measure_id=int(FoodMeasureId.GRAM),
        measure_singular="Gram",
        measure_plural="Grams",
        serving_quantity=122,
        serving_base_units=122,
        calories=50,
        fat=0,
        carbohydrate=12,
        protein=1,
        meal=int(MealType.LUNCH),
        user_id=USER_ID,
        sync_token=SYNC_TOKEN,
    )
    txn = read_message(read_message(data)[1][0])
    fle = read_message(txn[7][0])
    serving = read_message(fle[4][0])

    size_msg = read_message(serving[1][0])
    nutrients_msg = read_message(serving[2][0])

    serving_size = f64_from_uint(size_msg[2][0])
    base_units = f64_from_uint(nutrients_msg[1][0])
    assert serving_size == base_units == 122.0, (
        f"size {serving_size} != base_units {base_units}; bundle is malformed"
    )


# ---- Delete tombstone ----

def test_delete_bundle_sets_deleted_flag():
    data = build_delete_log_bundle(
        entry_uuid=b"\x01" * 16,
        food_uuid=b"\x02" * 16,
        food_name="Apple, Medium",
        meal=int(MealType.BREAKFAST),
        calories=95,
        user_id=USER_ID,
        sync_token=SYNC_TOKEN,
    )
    top = read_message(data)
    txn = read_message(top[1][0])
    fle = read_message(txn[7][0])
    ctx = read_message(fle[2][0])
    assert ctx[6][0] == 1, "delete must set tombstone flag to 1"
    assert ctx[5][0] == b"\x01" * 16, "entry uuid must round-trip"


# ---- Log food bundle ----

def test_log_food_bundle_reuses_measure():
    data, entry_uuid = build_log_food_bundle(
        food_uuid=b"\xab" * 16,
        food_name="Rice",
        food_product_name="Rice",
        measure_id=int(FoodMeasureId.GRAM),
        measure_singular="Gram",
        measure_plural="Grams",
        serving_quantity=200,
        serving_base_units=100,
        calories=130,
        fat=0.3,
        carbohydrate=28,
        protein=2.7,
        meal=int(MealType.LUNCH),
        user_id=USER_ID,
        sync_token=SYNC_TOKEN,
    )
    assert len(entry_uuid) == 16

    top = read_message(data)
    txn = read_message(top[1][0])
    fle = read_message(txn[7][0])
    serving = read_message(fle[4][0])
    size = read_message(serving[1][0])
    measure = read_message(size[4][0])
    assert measure[1][0] == int(FoodMeasureId.GRAM)
    assert measure[2][0] == b"Gram"


# ---- Recipe bundle ----

def test_create_recipe_bundle_has_recipe_and_ingredients():
    ing = [
        IngredientSpec(
            food_uuid=b"\x11" * 16,
            food_name="Apple",
            food_product_name="Apple",
            measure_id=int(FoodMeasureId.EACH),
            measure_singular="Each",
            measure_plural="Each",
            quantity=1,
            base_units=1,
            calories=95,
        ),
        IngredientSpec(
            food_uuid=b"\x22" * 16,
            food_name="Oats",
            food_product_name="Oats",
            measure_id=int(FoodMeasureId.GRAM),
            measure_singular="Gram",
            measure_plural="Grams",
            quantity=50,
            base_units=100,
            calories=389,
        ),
    ]
    data = build_create_recipe_bundle(
        recipe_uuid=b"\xff" * 16,
        recipe_name="mcp-test-recipe",
        ingredients=ing,
        user_id=USER_ID,
        sync_token=SYNC_TOKEN,
    )
    top = read_message(data)
    txn = read_message(top[1][0])

    # Recipe lives in field 4, repeated
    assert 4 in txn, "recipe missing from txn"
    recipe = read_message(txn[4][0])
    assert recipe[2][0] == b"mcp-test-recipe"
    assert recipe[4][0] == b"\xff" * 16
    assert recipe[5][0] == 0, "recipe should not be deleted"

    # RecipeIngredient lives in field 5, repeated, one per ingredient
    assert 5 in txn
    assert len(txn[5]) == 2
    ri_apple = read_message(txn[5][0])
    assert ri_apple[6][0] == b"\x11" * 16      # food uuid
    assert ri_apple[7][0] == b"\xff" * 16      # recipe uuid back-ref

    # ActiveFood wrappers: 1 for recipe + 1 per ingredient = 3
    assert len(txn[2]) == 3


def test_delete_recipe_bundle_sets_tombstone():
    data = build_delete_recipe_bundle(
        recipe_uuid=b"\xaa" * 16,
        recipe_name="mcp-test-recipe",
        user_id=USER_ID,
        sync_token=SYNC_TOKEN,
    )
    top = read_message(data)
    txn = read_message(top[1][0])
    assert 4 in txn
    recipe = read_message(txn[4][0])
    assert recipe[5][0] == 1, "recipe delete must set tombstone"
    assert recipe[4][0] == b"\xaa" * 16
