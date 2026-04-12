"""CLI entry point: python -m loseit_client <command> ..."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .auth import Auth, TokenStore
from .bundle import MealType
from .client import LoseItClient


def _client() -> LoseItClient:
    return LoseItClient(Auth())


def cmd_seed(args: argparse.Namespace) -> None:
    """Seed tokens from a captured login response (JSON file with fields
    access_token, refresh_token, user_id, expires_in, username)."""
    data = json.loads(Path(args.file).read_text())
    auth = Auth()
    auth.seed_from_capture(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        user_id=int(data["user_id"]),
        expires_in=int(data.get("expires_in", 1209600)),
        username=data.get("username", ""),
    )
    print(f"seeded tokens for user {auth.tokens.user_id}")


def cmd_refresh(args: argparse.Namespace) -> None:
    auth = Auth()
    t = auth.refresh()
    print(f"refreshed; new token expires at {t.expires_at}")


def cmd_log(args: argparse.Namespace) -> None:
    c = _client()
    meal = MealType[args.meal.upper()]
    entry = c.log_calories(
        name=args.name,
        calories=args.calories,
        meal=meal,
        fat=args.fat,
        carbohydrate=args.carbs,
        protein=args.protein,
    )
    print(f"logged {entry.name}: {entry.calories} cal under {meal.name}")
    print(f"  entry_uuid={entry.entry_uuid.hex()}")
    print(f"  food_uuid ={entry.food_uuid.hex()}")
    print(f"  ack txn ids: {entry.server_ack_txn_ids}")
    print(f"  resp fields: {entry.raw_response_fields}")


def cmd_delete(args: argparse.Namespace) -> None:
    c = _client()
    meal = MealType[args.meal.upper()]
    resp = c.delete_log_entry(
        entry_uuid=bytes.fromhex(args.entry_uuid),
        food_uuid=bytes.fromhex(args.food_uuid),
        food_name=args.name,
        meal=meal,
        calories=args.calories,
    )
    print(f"delete response: {resp}")


def cmd_barcode(args: argparse.Namespace) -> None:
    c = _client()
    data = c.barcode_lookup(args.barcode)
    print(f"{len(data)} bytes of protobuf:")
    print(data[:200].hex())


def main() -> None:
    p = argparse.ArgumentParser(prog="loseit_client")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("seed", help="seed tokens from a JSON file")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_seed)

    sp = sub.add_parser("refresh", help="rotate access token")
    sp.set_defaults(func=cmd_refresh)

    sp = sub.add_parser("log", help="log a calorie entry")
    sp.add_argument("--name", required=True)
    sp.add_argument("--calories", type=float, required=True)
    sp.add_argument("--meal", default="snacks", choices=["breakfast", "lunch", "dinner", "snacks"])
    sp.add_argument("--fat", type=float, default=0.0)
    sp.add_argument("--carbs", type=float, default=0.0)
    sp.add_argument("--protein", type=float, default=0.0)
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("delete", help="delete a log entry by uuid")
    sp.add_argument("--entry-uuid", required=True)
    sp.add_argument("--food-uuid", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--calories", type=float, required=True)
    sp.add_argument("--meal", default="snacks", choices=["breakfast", "lunch", "dinner", "snacks"])
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("barcode", help="look up a UPC")
    sp.add_argument("barcode")
    sp.set_defaults(func=cmd_barcode)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
