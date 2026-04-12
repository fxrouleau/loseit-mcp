# LoseIt Transaction Bundle — Wire Format

Reverse-engineered from the decompiled Android app. The canonical schema
lives in `UserDatabaseProtocol.java` inside the jadx decompile — a single
~77k-line protobuf-java (not protobuf-lite) compilation that kept all the
original class, field, and enum names.

Endpoint: `POST https://gateway.loseit.com/user/loseItTransactionBundle`
Content-Type: `application/x-protobuf`
Auth: `Authorization: Bearer <access_token>`

## LoseItGatewayTransactionBundleRequest

| # | Name             | Type                          |
|---|------------------|-------------------------------|
| 1 | transactions     | repeated LoseItGatewayTransaction |
| 2 | syncToken        | uint64 (wall-clock ms in practice) |
| 3 | (unknown)        | uint64                        |
| 4 | databaseUserId   | int32                         |

## LoseItGatewayTransaction (the mutation envelope)

Field 1 is a client-generated transaction id echoed back in the response so
the client can drop it from its pending queue. Fields 2–30 are repeated
message arrays, one slot per mutable entity type.

| # | Name                      | Type (repeated unless noted) |
|---|--------------------------|-------------------------------|
| 1 | id                       | int32 (client txn id)         |
| 2 | activeFoods              | ActiveFood                    |
| 3 | activeExercises          | ActiveExercise                |
| 4 | recipes                  | Recipe                        |
| 5 | recipeIngredients        | RecipeIngredient              |
| 7 | foodLogEntries           | FoodLogEntry                  |
| 11 | propertyBagEntries      | PropertyBagEntry              |
| 12 | dailyLogEntries         | DailyLogEntry                 |
| 14 | deletes                 | DeleteById                    |
| 16 | (unknown)               | int32                         |
| 18 | customGoalValues        | CustomGoalValue               |
| 21 | dailyUserValues         | DailyUserValue                |
| 23 | entityValues            | EntityValue (where override name/note lives) |

## FoodLogEntry

| # | Name    | Type                 |
|---|---------|----------------------|
| 2 | context | FoodLogEntryContext  |
| 3 | food    | FoodIdentifier       |
| 4 | serving | FoodServing          |

## FoodLogEntryContext

| #  | Name                  | Type         | Notes                          |
|----|-----------------------|--------------|--------------------------------|
| 1  | id                    | int32        | -1 for new                     |
| 2  | date                  | int32        | days since **2001-01-01 UTC**  |
| 3  | type                  | enum         | meal (see below)               |
| 4  | order                 | int32        |                                |
| 5  | uniqueId              | bytes (16)   | entry uuid                     |
| 6  | deleted               | bool         | tombstone flag                 |
| 7  | locallyMigratedRecord | bool         |                                |
| 8  | lastUpdated           | uint64 ms    |                                |
| 9  | pending               | bool         |                                |
| 10 | timestamp             | uint64 ms    |                                |
| 11 | timeZoneOffset        | float        |                                |
| 12 | created               | uint64 ms    |                                |

Meal enum (`FoodLogEntryContext.b`):

| value | meal      |
|-------|-----------|
| 0     | Breakfast |
| 1     | Lunch     |
| 2     | Dinner    |
| 3     | Snacks    |

## FoodIdentifier

| # | Name              | Type         |
|---|-------------------|--------------|
| 1 | id                | int32        |
| 2 | name              | string       |
| 3 | primaryFoodId     | int32        |
| 4 | foodCurationLevel | enum         |
| 5 | imageName         | string       |
| 6 | uniqueId          | string       |
| 7 | productName       | string       |
| 8 | uniqueIdBytes     | bytes (16)   |
| 9 | locale            | string       |
| 10 | sourceType       | enum         |

## FoodServing

| # | Name        | Type            |
|---|-------------|-----------------|
| 1 | servingSize | FoodServingSize |
| 2 | nutrients   | FoodNutrients   |

## FoodNutrients

| # | Name          | Type   |
|---|---------------|--------|
| 1 | baseUnits     | double |
| 2 | calories      | double |
| 3 | fat           | double |
| 4 | saturatedFat  | double |
| 5 | cholesterol   | double |
| 6 | sodium        | double |
| 7 | carbohydrates | double |
| 8 | fiber         | double |
| 9 | sugars        | double |
| 10 | protein      | double |
| 13 | nutrients    | map<string, double> |

## Sync protocol

- The client keeps a `syncToken` (uint64). First token comes from the initial
  `GET /user/database` response (the bulk baseline), then gets advanced each
  time the server replies to a bundle POST.
- Bundle response (`LoseItGatewayTransactionBundleResponse`):
  - field 1 = repeated transactionId (int32) — ack'd client txn ids
  - field 2 = repeated InvalidIdMapping — temp id → canonical id
  - field 3 = repeated LoseItGatewayTransaction — server-pushed mutations
  - field 4 = uint64 syncToken — new high watermark

## Day-number computation

LoseIt's internal date format is days since 2001-01-01 (UTC). Verified with
captured bundles: day 9233 ↔ 2026-04-12.

## Class-to-file index

All inside `com/loseit/server/database/UserDatabaseProtocol.java`:

| Class | Start line | writeTo |
|-------|-----------|---------|
| FoodIdentifier                         | 24013 | 25352 |
| FoodLogEntry                           | 25551 | 26319 |
| FoodLogEntryContext                    | 26456 | 27551 |
| FoodNutrients                          | 28464 | 29665 |
| FoodServing                            | 31197 | 31806 |
| LoseItGatewayTransaction               | 44210 | 51578 |
| LoseItGatewayTransactionBundleRequest  | 52164 | 52899 |
| LoseItGatewayTransactionBundleResponse | 53038 | 54405 |
| Recipe                                 | 62521 | 63969 |
| RecipeIngredient                       | 64205 | 65302 |

## Barcode lookup (separate host)

`GET https://food-search.prod.fitnowinc.com/food/barcode?barcode={upc}&preferred_locale={locale}`

Returns `com.fitnow.foundation.food.v1.Food` protobuf. Auth: static app
header `x-api-key: 70D980AE-FEA1-4058-82EC-E5C5FC229647` (Hb/a.java:151) —
not the user JWT.
