"""Quick sanity check: build a bundle, re-parse it, confirm it round-trips
and the top-level shape matches our captured references."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loseit_client.bundle import (
    CaloriesEntry,
    MealType,
    build_add_calories_bundle,
    parse_bundle_response,
)
from loseit_client.pb import read_message


def test_shape() -> None:
    entry = CaloriesEntry(name="mcp-test-e2e-1", calories=100, meal=MealType.SNACKS)
    data = build_add_calories_bundle(entry, user_id=34641935, sync_token=1_775_972_751_000)

    top = read_message(data)
    assert 1 in top, "expected repeated transaction at field 1"
    assert 2 in top, "expected sync_token at field 2"
    assert 4 in top, "expected database_user_id at field 4"
    assert top[4][0] == 34641935

    # Unpack the single transaction
    txn = read_message(top[1][0])
    assert 1 in txn, "expected transaction id"
    assert 2 in txn, "expected active_foods"
    assert 7 in txn, "expected food_log_entries"
    assert 23 in txn, "expected entity_values (name override)"

    # Unpack the log entry
    fle = read_message(txn[7][0])
    assert 2 in fle, "expected context"
    assert 3 in fle, "expected food"
    assert 4 in fle, "expected serving"

    ctx = read_message(fle[2][0])
    assert ctx[3][0] == int(MealType.SNACKS), "meal should be snacks"
    assert ctx[6][0] == 0, "not deleted"
    print(f"OK: bundle is {len(data)} bytes, top fields={sorted(top)}, "
          f"txn fields={sorted(txn)}, fle fields={sorted(fle)}")


if __name__ == "__main__":
    test_shape()
