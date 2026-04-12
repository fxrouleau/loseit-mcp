# loseit-client

Unofficial Python client for LoseIt!'s private sync API. Built by
reverse-engineering the Android app's protobuf transactions so you can
read and mutate your food log programmatically — auth, log, edit, delete,
barcode, text search, all E2E-tested against the real server.

## Install

```bash
pip install -e .
```

Depends on `requests` only.

## Setup: seed auth once

LoseIt's `/account/login` requires a reCAPTCHA token, so first-time login
can't be fully automated. Two options:

1. **Capture the app's login response** with a proxy (mitmproxy, Burp,
   etc.), extract the JSON body, and seed:

   ```bash
   python -m loseit_client seed /path/to/login_response.json
   ```

   The file needs `access_token`, `refresh_token`, `user_id`, `expires_in`,
   `username`.

2. **Solve the captcha yourself** and call `Auth.login_with_password(...)`
   directly with the token.

Subsequent refreshes go through `/auth/token`, which takes **no captcha**
and just rotates the access token. Tokens live ~14 days; the client
refreshes automatically when within 24h of expiry.

## Usage

```python
from loseit_client import LoseItClient, MealType, FoodMeasureId

c = LoseItClient()

# ---- reading ----
for row in c.get_day_log():                       # today
    print(row.meal, row.quantity, row.measure_name, row.food_name, row.calories)

import datetime
yesterday = c.get_day_log(datetime.date.today() - datetime.timedelta(days=1))

# ---- search the user's library ----
for f in c.search_foods("chicken breast"):
    print(f.name, f.last_serving_calories)

# ---- search LoseIt's full catalog ----
for f in c.search_catalog("banana", limit=5):
    print(f.name, f.nutrients.calories if f.nutrients else "?")

# ---- log a one-off calorie entry (for complex meals) ----
c.log_calories(
    name="Homemade chicken tikka masala",
    calories=650, fat=25, carbohydrate=60, protein=45,
    meal=MealType.DINNER,
)

# ---- log an existing food from the user's library ----
apple = c.search_foods("Apple, Medium")[0]
c.log_food(food_uuid=apple.food_uuid, meal=MealType.BREAKFAST, quantity=1)

# ---- log something from the catalog or a barcode ----
skittles = c.barcode_lookup("058496464615")
# Inspect available units:
for i, s in enumerate(skittles.servings):
    print(i, s.size, s.measure_plural, s.measure_id)
# Log 20g (half the 40g serving)
c.log_food_from_catalog(skittles, meal=MealType.SNACKS, serving_index=1, quantity=20)

# ---- edit ----
c.edit_calories(entry_uuid=..., food_uuid=..., name="...", calories=500, meal=...)
c.edit_food_entry(entry_uuid=..., food_uuid=..., meal=..., quantity=2)

# ---- delete ----
c.delete_log_entry(entry_uuid=..., food_uuid=..., food_name="...",
                   meal=..., calories=...)
```

## What's implemented

| Capability                        | Method                         |
|-----------------------------------|--------------------------------|
| Login with captcha                | `Auth.login_with_password`     |
| Seed tokens from capture          | `Auth.seed_from_capture`       |
| Refresh access token (no captcha) | `Auth.refresh`                 |
| Read log for any date             | `client.get_day_log(date)`     |
| Search user's food library        | `client.search_foods`          |
| Search LoseIt's full catalog      | `client.search_catalog`        |
| Search custom recipes             | `client.search_recipes`        |
| Barcode lookup → typed Food       | `client.barcode_lookup`        |
| Log one-off calories + macros     | `client.log_calories`          |
| Log food from user's library      | `client.log_food`              |
| Log food from catalog / barcode   | `client.log_food_from_catalog` |
| Edit calories entry               | `client.edit_calories`         |
| Edit food entry                   | `client.edit_food_entry`       |
| Delete log entry                  | `client.delete_log_entry`      |
| All 48 `FoodMeasureId` units      | `loseit_client.FoodMeasureId`  |

## What's NOT implemented (deliberate)

- **Create custom recipe** — the wire format is mapped (`docs/wire-format.md`)
  and captured, but the builder isn't written. User's earlier direction
  was "maybe useful later." If you need it, ping.
- **log_food with unit override for library foods** — ActiveFoods only
  stores one measure per food, so overriding to grams when the food was
  last logged as "Each" needs a fresh catalog lookup first. Use
  `log_food_from_catalog` for that.
- **Weight / exercise / water / fasting** — user handles these in other
  apps (Withings, etc.).
- **Batch mutations** — `build_*_bundle` primitives in `bundle.py` support
  multi-entry transactions, but the high-level client sends one at a time.
- **Recipe ingredient editing** — same reason as recipe create.

## Architecture

```
loseit_client/
  pb.py            minimal protobuf wire codec, no deps
  bundle.py        builds LoseItGatewayTransactionBundleRequest payloads
  food_search.py   decodes com.fitnow.foundation.food.v1 Food responses
  auth.py          login + refresh + token cache at ~/.loseit_client/tokens.json
  transport.py     requests session, device headers, auth injection
  db.py            SQLite read view over /user/database snapshot
  client.py        high-level API (LoseItClient)
  __main__.py      minimal CLI: seed / refresh / log / delete / barcode
docs/
  wire-format.md   protobuf field map (reverse-engineered from jadx)
```

Three hosts:

- `gateway.loseit.com` — JWT-auth'd POST-only gateway for transaction
  bundles + `/user/database` snapshot download.
- `sync.loseit.com` — `/account/login`, `/auth/token`, `/me/*`.
- `food-search.prod.fitnowinc.com` — barcode + text search, uses a
  **static** `x-api-key` header (`70D980AE-FEA1-...`), not the user JWT.

## Caveats

- `/user/database` is a 2.5MB SQLite download per call. Reads go through
  that snapshot — there's no lighter GET endpoint. For frequent reads,
  stash the `UserDatabase` instance and call `client.refresh_database()`
  only when you know another device has written.
- Sync tokens on the wire are `uint64` — we use `int(time.time()*1000)`
  monotonically, matching what the native app sends.
- `date` field in `FoodLogEntryContext` is days since `2000-12-31`
  (verified against captured bundles).
