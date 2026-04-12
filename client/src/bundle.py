"""Build LoseIt transaction bundles on the wire.

Schema source: `UserDatabaseProtocol.java` in the decompiled APK. See
`docs/wire-format.md` in this repo for the authoritative field map. All
message names in this file match the Java class names.
"""
from __future__ import annotations

import datetime as dt
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum

from .pb import Writer


class MealType(IntEnum):
    BREAKFAST = 0
    LUNCH = 1
    DINNER = 2
    SNACKS = 3


class FoodMeasureId(IntEnum):
    """Full `cb.FoodMeasureType` enum from the decompiled app. Value is the
    protobuf enum number — this is what goes on the wire in FoodMeasure.id.
    """
    UNSPECIFIED = 0
    TEASPOON = 1
    TABLESPOON = 2
    CUP = 3
    PIECE = 4
    EACH = 5
    OUNCE = 6
    POUND = 7
    GRAM = 8
    KILOGRAM = 9
    FLUID_OUNCE = 10
    MILLILITER = 11
    LITER = 12
    GALLON = 13
    PINT = 14
    QUART = 15
    MILLIGRAM = 16
    MICROGRAM = 17
    INTAKE = 18
    BOTTLE = 20
    BOX = 21
    CAN = 22
    CUBE = 24
    JAR = 25
    STICK = 26
    TABLET = 27
    SLICE = 30
    SERVING = 31
    CAN300 = 32
    CAN303 = 33
    CAN401 = 34
    CAN404 = 35
    INDIVIDUAL_PACKAGE = 36
    SCOOP = 37
    METRIC_CUP = 38
    DRY_CUP = 39
    IMPERIAL_FLUID_OUNCE = 40
    IMPERIAL_GALLON = 41
    IMPERIAL_QUART = 42
    IMPERIAL_PINT = 43
    TABLESPOON_AUS = 44
    DESSERT_SPOON = 45
    POT = 46
    PUNNET = 47
    AS_ENTERED = 99
    CONTAINER = 640
    PACKAGE = 650
    POUCH = 660


# Human-readable singular/plural names for each measure. Used when
# constructing the FoodMeasure submessage on the wire and when displaying
# serving sizes to the user.
MEASURE_LABELS: dict[int, tuple[str, str]] = {
    FoodMeasureId.UNSPECIFIED: ("", ""),
    FoodMeasureId.TEASPOON: ("Teaspoon", "Teaspoons"),
    FoodMeasureId.TABLESPOON: ("Tablespoon", "Tablespoons"),
    FoodMeasureId.CUP: ("Cup", "Cups"),
    FoodMeasureId.PIECE: ("Piece", "Pieces"),
    FoodMeasureId.EACH: ("Each", "Each"),
    FoodMeasureId.OUNCE: ("Ounce", "Ounces"),
    FoodMeasureId.POUND: ("Pound", "Pounds"),
    FoodMeasureId.GRAM: ("Gram", "Grams"),
    FoodMeasureId.KILOGRAM: ("Kilogram", "Kilograms"),
    FoodMeasureId.FLUID_OUNCE: ("Fluid Ounce", "Fluid Ounces"),
    FoodMeasureId.MILLILITER: ("Milliliter", "Milliliters"),
    FoodMeasureId.LITER: ("Liter", "Liters"),
    FoodMeasureId.GALLON: ("Gallon", "Gallons"),
    FoodMeasureId.PINT: ("Pint", "Pints"),
    FoodMeasureId.QUART: ("Quart", "Quarts"),
    FoodMeasureId.MILLIGRAM: ("Milligram", "Milligrams"),
    FoodMeasureId.MICROGRAM: ("Microgram", "Micrograms"),
    FoodMeasureId.INTAKE: ("Intake", "Intakes"),
    FoodMeasureId.BOTTLE: ("Bottle", "Bottles"),
    FoodMeasureId.BOX: ("Box", "Boxes"),
    FoodMeasureId.CAN: ("Can", "Cans"),
    FoodMeasureId.CUBE: ("Cube", "Cubes"),
    FoodMeasureId.JAR: ("Jar", "Jars"),
    FoodMeasureId.STICK: ("Stick", "Sticks"),
    FoodMeasureId.TABLET: ("Tablet", "Tablets"),
    FoodMeasureId.SLICE: ("Slice", "Slices"),
    FoodMeasureId.SERVING: ("Serving", "Servings"),
    FoodMeasureId.CAN300: ("Can 300", "Cans 300"),
    FoodMeasureId.CAN303: ("Can 303", "Cans 303"),
    FoodMeasureId.CAN401: ("Can 401", "Cans 401"),
    FoodMeasureId.CAN404: ("Can 404", "Cans 404"),
    FoodMeasureId.INDIVIDUAL_PACKAGE: ("Individual Package", "Individual Packages"),
    FoodMeasureId.SCOOP: ("Scoop", "Scoops"),
    FoodMeasureId.METRIC_CUP: ("Metric Cup", "Metric Cups"),
    FoodMeasureId.DRY_CUP: ("Dry Cup", "Dry Cups"),
    FoodMeasureId.IMPERIAL_FLUID_OUNCE: ("Imperial Fluid Ounce", "Imperial Fluid Ounces"),
    FoodMeasureId.IMPERIAL_GALLON: ("Imperial Gallon", "Imperial Gallons"),
    FoodMeasureId.IMPERIAL_QUART: ("Imperial Quart", "Imperial Quarts"),
    FoodMeasureId.IMPERIAL_PINT: ("Imperial Pint", "Imperial Pints"),
    FoodMeasureId.TABLESPOON_AUS: ("Tablespoon (AUS)", "Tablespoons (AUS)"),
    FoodMeasureId.DESSERT_SPOON: ("Dessert Spoon", "Dessert Spoons"),
    FoodMeasureId.POT: ("Pot", "Pots"),
    FoodMeasureId.PUNNET: ("Punnet", "Punnets"),
    FoodMeasureId.AS_ENTERED: ("", ""),
    FoodMeasureId.CONTAINER: ("Container", "Containers"),
    FoodMeasureId.PACKAGE: ("Package", "Packages"),
    FoodMeasureId.POUCH: ("Pouch", "Pouches"),
}


def measure_labels(measure_id: int) -> tuple[str, str]:
    """(singular, plural) for a measure id. Empty strings for unknown."""
    return MEASURE_LABELS.get(measure_id, ("", ""))


# `protobuf-java`'s writeInt32 encodes -1 as 10 bytes (sign-extended to
# uint64), NOT 5 bytes, so we pass -1 to the varint encoder and let it mask.
UNSET_INT = -1
UNSET_U64 = -1

# LoseIt's internal `date_day` is days since 2000-12-31 (so 2001-01-01 = 1).
# Verified against captured bundles: day 9233 ↔ 2026-04-12.
LOSEIT_EPOCH = dt.date(2000, 12, 31)


def days_since_loseit_epoch(when: dt.date | None = None) -> int:
    return ((when or dt.date.today()) - LOSEIT_EPOCH).days


def new_uuid16() -> bytes:
    return uuid.uuid4().bytes


def now_ms() -> int:
    return int(time.time() * 1000)


def new_txn_id() -> int:
    """Client-assigned transaction id. Servers use this to ack transactions.

    Needs to be unique within the client's pending queue. A random 31-bit int
    is plenty since the server dedupes by echoing it back in the response.
    """
    return int.from_bytes(os.urandom(4), "big") & 0x7fffffff


# ---------- FoodIdentifier / FoodServing / FoodNutrients ----------

def food_identifier(
    *,
    name: str,
    product_name: str,
    unique_id_bytes: bytes,
    primary_food_id: int = UNSET_INT,
    image_name: str = "",
    unique_id: str = "",
    locale: str = "",
) -> bytes:
    w = Writer()
    w.varint(1, UNSET_INT)
    w.string(2, name)
    w.varint(3, primary_food_id)
    w.varint(4, 0)                  # foodCurationLevel = default
    w.string(5, image_name)
    if unique_id:
        w.string(6, unique_id)
    else:
        # Captured bundles use the short category/display label here; reuse
        # product_name as a sensible default.
        w.string(6, product_name)
    w.string(7, product_name)
    w.bytes_(8, unique_id_bytes)
    return w.build()


def food_measure(measure_id: int, singular: str, plural: str) -> bytes:
    w = Writer()
    w.varint(1, measure_id)
    w.string(2, singular)
    w.string(3, plural)
    return w.build()


def food_serving_size(
    description: str,
    size: float,
    size_converted: float | None,
    measure: bytes,
    quick_add: bool = True,
) -> bytes:
    w = Writer()
    w.string(1, description)
    w.f64(2, size)
    w.f64(3, size_converted if size_converted is not None else size)
    w.submsg(4, measure)
    w.varint(5, 1 if quick_add else 0)
    return w.build()


def food_nutrients(
    *,
    base_units: float,
    calories: float,
    fat: float = 0.0,
    saturated_fat: float = 0.0,
    cholesterol: float = 0.0,
    sodium: float = 0.0,
    carbohydrates: float = 0.0,
    fiber: float = 0.0,
    sugars: float = 0.0,
    protein: float = 0.0,
    extra: dict[str, float] | None = None,
) -> bytes:
    w = Writer()
    w.f64(1, base_units)
    w.f64(2, calories)
    w.f64(3, fat)
    w.f64(4, saturated_fat)
    w.f64(5, cholesterol)
    w.f64(6, sodium)
    w.f64(7, carbohydrates)
    w.f64(8, fiber)
    w.f64(9, sugars)
    w.f64(10, protein)
    # Field 13 is a proto3 map<string, double> on the wire, encoded as a
    # repeated message where each entry has key=1 (string), value=2 (double).
    nutrients: dict[str, float] = {
        "energy": calories,
        "fat": fat,
        "saturated_fat": saturated_fat,
        "cholesterol": cholesterol,
        "sodium": sodium,
        "carbohydrate": carbohydrates,
        "fiber": fiber,
        "sugar": sugars,
        "protein": protein,
    }
    if extra:
        nutrients.update(extra)
    for k, v in nutrients.items():
        entry = Writer().string(1, k).f64(2, v).build()
        w.submsg(13, entry)
    return w.build()


def food_serving(size: bytes, nutrients: bytes) -> bytes:
    return Writer().submsg(1, size).submsg(2, nutrients).build()


# ---------- FoodLogEntry + FoodLogEntryContext ----------

def food_log_entry_context(
    *,
    date_day: int,
    meal: int,
    unique_id: bytes,
    deleted: bool = False,
    order: int = 0,
    timestamp_ms: int | None = None,
) -> bytes:
    now = now_ms()
    w = Writer()
    w.varint(1, UNSET_INT)                       # id
    w.varint(2, date_day)                        # date (days since 2001-01-01)
    w.varint(3, meal)                            # type (meal enum)
    w.varint(4, order)                           # order within meal
    w.bytes_(5, unique_id)                       # uniqueId
    w.varint(6, 1 if deleted else 0)             # deleted
    w.varint(7, 0)                               # locallyMigratedRecord
    w.varint(8, now)                             # lastUpdated
    w.varint(9, 0)                               # pending
    # field 10 = timestamp — observed as 0 in captures, skip
    # field 11 = timeZoneOffset — observed absent
    w.varint(12, timestamp_ms if timestamp_ms is not None else now)  # created
    return w.build()


def food_log_entry(
    *, context: bytes, food: bytes, serving: bytes
) -> bytes:
    w = Writer()
    w.submsg(2, context)
    w.submsg(3, food)
    w.submsg(4, serving)
    return w.build()


# ---------- ActiveFood ----------

def active_food(
    *,
    food_identifier_bytes: bytes,
    food_serving_bytes: bytes,
    created_at_ms: int,
    primary_food_db_id: int = 221568,
) -> bytes:
    """`ActiveFood` is the user's "this food exists in my personal library"
    record. Every food referenced by a log entry gets one of these posted
    alongside the log, even for standard catalog foods.

    Fields (from UserDatabaseProtocol.java line 4653+):
      1: int32 id       -> -1 for new
      2: FoodIdentifier
      3: FoodServing
      4: bool            -> true (visible/enabled)
      5: int32           -> primary food db id (observed 221568)
      6: int32           -> small int counter (observed 3-41, meaning unclear)
      7: bool            -> true
      8: bool            -> false
      9: uint64          -> created/lastUpdated ms
    """
    w = Writer()
    w.varint(1, UNSET_INT)
    w.submsg(2, food_identifier_bytes)
    w.submsg(3, food_serving_bytes)
    w.varint(4, 1)
    w.varint(5, primary_food_db_id)
    w.varint(6, 3)
    w.varint(7, 1)
    w.varint(8, 0)
    w.varint(9, created_at_ms)
    return w.build()


# ---------- EntityValue (log name / notes overrides) ----------

def entity_value(
    *, entity_uuid: bytes, entity_type: int, key: str, value: str, created_at_ms: int
) -> bytes:
    """EntityValue is a key/value pair attached to another entity by uuid.

    Observed uses:
      - kind="FoodLogOverrideName", value=<display name>
      - kind="FoodLogTypeExtra",    value=<meal as string>
    """
    w = Writer()
    w.bytes_(1, entity_uuid)
    w.varint(2, entity_type)
    w.string(3, key)
    w.string(4, value)
    w.varint(5, 0)
    w.varint(6, created_at_ms)
    return w.build()


# ---------- LoseItGatewayTransaction envelope ----------

def gateway_transaction(
    *,
    txn_id: int,
    active_foods: list[bytes] | None = None,
    food_log_entries: list[bytes] | None = None,
    entity_values: list[bytes] | None = None,
    unknown_16: int = 2,
) -> bytes:
    w = Writer()
    w.varint(1, txn_id)
    for af in active_foods or []:
        w.submsg(2, af)
    for fle in food_log_entries or []:
        w.submsg(7, fle)
    w.varint(16, unknown_16)
    for ev in entity_values or []:
        w.submsg(23, ev)
    return w.build()


def gateway_bundle_request(
    *,
    transactions: list[bytes],
    sync_token: int,
    database_user_id: int,
) -> bytes:
    w = Writer()
    for t in transactions:
        w.submsg(1, t)
    w.varint(2, sync_token)
    w.varint(4, database_user_id)
    return w.build()


# ---------- High-level convenience builders ----------

@dataclass
class CaloriesEntry:
    name: str
    calories: float
    fat: float = 0.0
    carbohydrate: float = 0.0
    protein: float = 0.0
    meal: MealType = MealType.SNACKS
    day: dt.date | None = None          # default: today
    entry_uuid: bytes = field(default_factory=new_uuid16)
    food_uuid: bytes = field(default_factory=new_uuid16)


def build_add_calories_bundle(
    entry: CaloriesEntry,
    *,
    user_id: int,
    sync_token: int,
) -> bytes:
    """One-shot "Add Calories" flow.

    Creates a throwaway Food called "Calories" with the user-supplied macros,
    wraps it in an ActiveFood record, and logs a single unit of it under the
    chosen meal. An EntityValue override supplies the display name so the UI
    shows e.g. "mcp-test-e2e-1" instead of "Calories".
    """
    now = now_ms()
    day = days_since_loseit_epoch(entry.day)

    food_id = food_identifier(
        name="Calories",
        product_name="Calories",
        unique_id_bytes=entry.food_uuid,
    )
    measure = food_measure(FoodMeasureId.EACH, "Each", "Each")
    size = food_serving_size(
        description=f"{int(entry.calories)} Each",
        size=float(entry.calories),
        size_converted=float(entry.calories),
        measure=measure,
    )
    nutrients = food_nutrients(
        base_units=float(entry.calories),
        calories=float(entry.calories),
        fat=float(entry.fat),
        carbohydrates=float(entry.carbohydrate),
        protein=float(entry.protein),
    )
    serving = food_serving(size, nutrients)

    ctx = food_log_entry_context(
        date_day=day,
        meal=int(entry.meal),
        unique_id=entry.entry_uuid,
    )
    fle = food_log_entry(context=ctx, food=food_id, serving=serving)

    af = active_food(
        food_identifier_bytes=food_id,
        food_serving_bytes=serving,
        created_at_ms=now,
    )
    name_override = entity_value(
        entity_uuid=entry.entry_uuid,
        entity_type=9,
        key="FoodLogOverrideName",
        value=entry.name,
        created_at_ms=now,
    )

    txn = gateway_transaction(
        txn_id=new_txn_id(),
        active_foods=[af],
        food_log_entries=[fle],
        entity_values=[name_override],
    )
    return gateway_bundle_request(
        transactions=[txn],
        sync_token=sync_token,
        database_user_id=user_id,
    )


def build_log_food_bundle(
    *,
    food_uuid: bytes,
    food_name: str,
    food_product_name: str,
    measure_id: int,
    measure_singular: str,
    measure_plural: str,
    serving_quantity: float,
    serving_base_units: float,
    calories: float,
    fat: float,
    carbohydrate: float,
    protein: float,
    meal: int,
    user_id: int,
    sync_token: int,
    day: dt.date | None = None,
    entry_uuid: bytes | None = None,
    extra_nutrients: dict[str, float] | None = None,
    order: int = 0,
) -> tuple[bytes, bytes]:
    """Log an existing food from the user's library.

    Returns (bundle_bytes, entry_uuid). The food is referenced by uuid;
    we re-send its identifier + serving + nutrients inline, mimicking what
    the native app does for a catalog food log.

    `serving_base_units` is FoodNutrients.baseUnits — the "per this amount"
    field that tells the server how much of the food the nutrition values
    correspond to (e.g. 1 for "1 Each", 100 for "100 grams").
    """
    day_num = days_since_loseit_epoch(day)
    entry_uuid = entry_uuid or new_uuid16()
    now = now_ms()

    food_id = food_identifier(
        name=food_name,
        product_name=food_product_name,
        unique_id_bytes=food_uuid,
    )
    measure = food_measure(measure_id, measure_singular, measure_plural)
    size = food_serving_size(
        description=_serving_description(serving_quantity, measure_singular, measure_plural),
        size=float(serving_quantity),
        size_converted=float(serving_quantity),
        measure=measure,
    )
    nutrients = food_nutrients(
        base_units=float(serving_base_units),
        calories=float(calories),
        fat=float(fat),
        carbohydrates=float(carbohydrate),
        protein=float(protein),
        extra=extra_nutrients,
    )
    serving = food_serving(size, nutrients)

    ctx = food_log_entry_context(
        date_day=day_num,
        meal=meal,
        unique_id=entry_uuid,
        order=order,
    )
    fle = food_log_entry(context=ctx, food=food_id, serving=serving)
    af = active_food(
        food_identifier_bytes=food_id,
        food_serving_bytes=serving,
        created_at_ms=now,
    )
    txn = gateway_transaction(
        txn_id=new_txn_id(),
        active_foods=[af],
        food_log_entries=[fle],
    )
    return (
        gateway_bundle_request(
            transactions=[txn], sync_token=sync_token, database_user_id=user_id
        ),
        entry_uuid,
    )


def _serving_description(qty: float, singular: str, plural: str) -> str:
    """Mimic the native app's serving description format."""
    if qty == 1.0:
        return f"1 {singular}"
    if qty == int(qty):
        return f"{int(qty)} {plural}"
    return f"{qty:g} {plural}"


def _recipe(
    *,
    name: str,
    unique_id: bytes,
    total_servings: float,
    recipe_measure_id: int,
    portion_quantity: float,
    portion_measure_id: int,
    deleted: bool,
    created_ms: int,
    last_updated_ms: int,
) -> bytes:
    """Recipe message (field 4 of a LoseItGatewayTransaction).

    Wire layout verified against a captured create-recipe bundle; fields
    8-11 encode (totalServings, recipeMeasureId, portionQuantity,
    portionMeasureId). Field 16 is a bool that was set to 1 in captures —
    unknown purpose but required for the server to accept the record.
    """
    w = Writer()
    w.varint(1, UNSET_INT)
    w.string(2, name)
    w.varint(3, 1)                   # visible
    w.bytes_(4, unique_id)
    w.varint(5, 1 if deleted else 0)
    w.varint(6, 0)
    w.varint(7, last_updated_ms)
    w.f64(8, total_servings)
    w.varint(9, recipe_measure_id)
    w.f64(10, portion_quantity)
    w.varint(11, portion_measure_id)
    w.bytes_(12, b"")                 # imageName
    w.varint(13, created_ms)
    w.varint(16, 1)
    return w.build()


def _recipe_ingredient(
    *,
    food_identifier_bytes: bytes,
    food_serving_bytes: bytes,
    ingredient_uuid: bytes,
    food_uuid: bytes,
    recipe_uuid: bytes,
    last_updated_ms: int,
) -> bytes:
    """RecipeIngredient message (field 5 of a LoseItGatewayTransaction).

    Captured field layout (differs slightly from the agent's first-pass
    mapping, re-verified here):
      1 id int32           always -1 on new ingredients
      2 recipeId int32     always -1 on new (server assigns)
      3 food FoodIdentifier
      4 serving FoodServing
      5 uniqueId bytes     ingredient's own uuid
      6 foodUniqueId bytes the ingredient food's uuid
      7 recipeUniqueId bytes links back to the Recipe
      8 deleted bool
      9 bool               (unknown, 0 in captures)
      10 lastUpdated uint64
    """
    w = Writer()
    w.varint(1, UNSET_INT)
    w.varint(2, UNSET_INT)
    w.submsg(3, food_identifier_bytes)
    w.submsg(4, food_serving_bytes)
    w.bytes_(5, ingredient_uuid)
    w.bytes_(6, food_uuid)
    w.bytes_(7, recipe_uuid)
    w.varint(8, 0)
    w.varint(9, 0)
    w.varint(10, last_updated_ms)
    return w.build()


def gateway_transaction_with_recipe(
    *,
    txn_id: int,
    active_foods: list[bytes],
    recipes: list[bytes],
    recipe_ingredients: list[bytes],
    unknown_16: int = 2,
) -> bytes:
    """Extended transaction builder that populates the Recipe (field 4) and
    RecipeIngredient (field 5) repeated slots alongside activeFoods.
    """
    w = Writer()
    w.varint(1, txn_id)
    for af in active_foods:
        w.submsg(2, af)
    for r in recipes:
        w.submsg(4, r)
    for ri in recipe_ingredients:
        w.submsg(5, ri)
    w.varint(16, unknown_16)
    return w.build()


@dataclass
class IngredientSpec:
    """One ingredient for build_create_recipe_bundle.

    Holds enough food info to construct an inline FoodIdentifier +
    FoodServing for the ingredient. These fields come straight from a
    FoodRow returned by UserDatabase.search_foods / get_food_by_uuid.
    """
    food_uuid: bytes
    food_name: str
    food_product_name: str
    measure_id: int
    measure_singular: str
    measure_plural: str
    quantity: float           # how much of this ingredient
    base_units: float         # the food's base_units (from ActiveFoods.LastServingBaseUnits)
    calories: float           # per serving (will scale by quantity/base_units)
    fat: float = 0.0
    carbohydrate: float = 0.0
    protein: float = 0.0


def build_create_recipe_bundle(
    *,
    recipe_uuid: bytes,
    recipe_name: str,
    ingredients: list[IngredientSpec],
    user_id: int,
    sync_token: int,
    total_servings: float = 1.0,
) -> bytes:
    """Assemble a bundle that creates a recipe with the given ingredients.

    The native app sends four things in one transaction:
      * the Recipe record itself
      * one ActiveFood that makes the recipe discoverable as a food
      * one ActiveFood per ingredient food (defensive persistence)
      * one RecipeIngredient per ingredient linking the food to the recipe

    Nutrition totals are summed from the ingredients and attached to the
    recipe's wrapper ActiveFood so it can be logged directly afterwards.
    """
    now = now_ms()
    active_foods: list[bytes] = []
    recipe_ingredient_msgs: list[bytes] = []

    total_calories = 0.0
    total_fat = 0.0
    total_carbohydrate = 0.0
    total_protein = 0.0

    for ing in ingredients:
        scale = ing.quantity / ing.base_units if ing.base_units else 1.0
        ing_calories = ing.calories * scale
        ing_fat = ing.fat * scale
        ing_carb = ing.carbohydrate * scale
        ing_protein = ing.protein * scale
        total_calories += ing_calories
        total_fat += ing_fat
        total_carbohydrate += ing_carb
        total_protein += ing_protein

        ing_food_id = food_identifier(
            name=ing.food_name,
            product_name=ing.food_product_name,
            unique_id_bytes=ing.food_uuid,
        )
        ing_measure = food_measure(
            ing.measure_id, ing.measure_singular, ing.measure_plural or ing.measure_singular
        )
        ing_size = food_serving_size(
            description=f"{ing.quantity:g} {ing.measure_plural or ing.measure_singular}",
            size=ing.quantity,
            size_converted=ing.quantity,
            measure=ing_measure,
        )
        ing_nutrients = food_nutrients(
            base_units=ing.quantity,
            calories=ing_calories,
            fat=ing_fat,
            carbohydrates=ing_carb,
            protein=ing_protein,
        )
        ing_serving = food_serving(ing_size, ing_nutrients)

        active_foods.append(
            active_food(
                food_identifier_bytes=ing_food_id,
                food_serving_bytes=ing_serving,
                created_at_ms=now,
            )
        )
        recipe_ingredient_msgs.append(
            _recipe_ingredient(
                food_identifier_bytes=ing_food_id,
                food_serving_bytes=ing_serving,
                ingredient_uuid=new_uuid16(),
                food_uuid=ing.food_uuid,
                recipe_uuid=recipe_uuid,
                last_updated_ms=now,
            )
        )

    # Wrapper ActiveFood for the recipe itself — mirrors the native flow
    # and lets you log the recipe via the ordinary `log_food` path later.
    recipe_food_id = food_identifier(
        name=recipe_name,
        product_name="Recipe",
        unique_id_bytes=recipe_uuid,
    )
    recipe_measure = food_measure(FoodMeasureId.SERVING, "Serving", "Servings")
    recipe_size = food_serving_size(
        description="1 Serving",
        size=1.0,
        size_converted=1.0,
        measure=recipe_measure,
    )
    recipe_nutrients = food_nutrients(
        base_units=1.0,
        calories=total_calories,
        fat=total_fat,
        carbohydrates=total_carbohydrate,
        protein=total_protein,
    )
    recipe_serving = food_serving(recipe_size, recipe_nutrients)
    active_foods.insert(
        0,
        active_food(
            food_identifier_bytes=recipe_food_id,
            food_serving_bytes=recipe_serving,
            created_at_ms=now,
        ),
    )

    recipe_msg = _recipe(
        name=recipe_name,
        unique_id=recipe_uuid,
        total_servings=total_servings,
        recipe_measure_id=FoodMeasureId.SERVING,
        portion_quantity=1.0,
        portion_measure_id=FoodMeasureId.SERVING,
        deleted=False,
        created_ms=now,
        last_updated_ms=now,
    )

    txn = gateway_transaction_with_recipe(
        txn_id=new_txn_id(),
        active_foods=active_foods,
        recipes=[recipe_msg],
        recipe_ingredients=recipe_ingredient_msgs,
    )
    return gateway_bundle_request(
        transactions=[txn], sync_token=sync_token, database_user_id=user_id
    )


def build_delete_recipe_bundle(
    *,
    recipe_uuid: bytes,
    recipe_name: str,
    user_id: int,
    sync_token: int,
) -> bytes:
    """Tombstone a recipe by re-sending it with deleted=1."""
    now = now_ms()
    recipe_msg = _recipe(
        name=recipe_name,
        unique_id=recipe_uuid,
        total_servings=1.0,
        recipe_measure_id=FoodMeasureId.SERVING,
        portion_quantity=1.0,
        portion_measure_id=FoodMeasureId.SERVING,
        deleted=True,
        created_ms=now,
        last_updated_ms=now,
    )
    txn = gateway_transaction_with_recipe(
        txn_id=new_txn_id(),
        active_foods=[],
        recipes=[recipe_msg],
        recipe_ingredients=[],
    )
    return gateway_bundle_request(
        transactions=[txn], sync_token=sync_token, database_user_id=user_id
    )


def build_delete_log_bundle(
    *,
    entry_uuid: bytes,
    food_uuid: bytes,
    food_name: str,
    meal: int,
    calories: float,
    user_id: int,
    sync_token: int,
    fat: float = 0.0,
    carbohydrate: float = 0.0,
    protein: float = 0.0,
    day: dt.date | None = None,
    measure_id: int = FoodMeasureId.EACH,
    measure_singular: str = "Each",
    measure_plural: str = "Each",
) -> bytes:
    """Delete an existing food log entry.

    The LoseIt sync protocol uses tombstones: the delete is the full
    FoodLogEntry re-sent with the `deleted` bool flipped on. All of the
    food/serving content has to be there — the server resolves by uuid.
    """
    day_num = days_since_loseit_epoch(day)
    food_id = food_identifier(
        name=food_name,
        product_name=food_name,
        unique_id_bytes=food_uuid,
    )
    measure = food_measure(measure_id, measure_singular, measure_plural)
    size = food_serving_size(
        description=f"{int(calories)} {measure_singular}",
        size=float(calories),
        size_converted=float(calories),
        measure=measure,
    )
    nutrients = food_nutrients(
        base_units=float(calories),
        calories=float(calories),
        fat=float(fat),
        carbohydrates=float(carbohydrate),
        protein=float(protein),
    )
    serving = food_serving(size, nutrients)

    ctx = food_log_entry_context(
        date_day=day_num,
        meal=meal,
        unique_id=entry_uuid,
        deleted=True,
    )
    fle = food_log_entry(context=ctx, food=food_id, serving=serving)
    txn = gateway_transaction(
        txn_id=new_txn_id(),
        food_log_entries=[fle],
    )
    return gateway_bundle_request(
        transactions=[txn], sync_token=sync_token, database_user_id=user_id
    )


# ---------- Response parser ----------

def parse_bundle_response(data: bytes) -> dict:
    """Minimal decoder for LoseItGatewayTransactionBundleResponse.

    Returns {ack_txn_ids, sync_token, raw_fields} for debugging / sync
    bookkeeping. Extend as needed.
    """
    from .pb import read_message
    top = read_message(data)
    return {
        "ack_txn_ids": top.get(1, []),
        "sync_token": top.get(4, [0])[0] if 4 in top else None,
        "raw_fields": sorted(top.keys()),
    }
