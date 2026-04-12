"""Seed and expose a `LoseItClient` instance for the MCP server.

The container starts with LoseIt tokens in env vars (first boot) or a
persisted JSON file in the data volume (subsequent boots). Tokens rotated
by the refresh path are written back to the data volume so restarts stay
authenticated.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from loseit_client.auth import Auth, DEFAULT_HEADERS, TokenStore, Tokens
from loseit_client.client import LoseItClient

from .config import Settings


def _token_path(data_dir: Path) -> Path:
    return data_dir / "loseit_tokens.json"


def build_client(settings: Settings) -> LoseItClient:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    cache = _token_path(settings.data_dir)
    store = TokenStore(cache)
    auth = Auth(store=store)

    if auth.tokens is None:
        # First boot — seed from env. Only refresh_token + user_id are
        # strictly required; access_token can be absent and we'll pull a
        # new one on the first /auth/token refresh.
        if not settings.loseit_refresh_token or not settings.loseit_user_id:
            raise RuntimeError(
                "No cached LoseIt tokens and missing MCP_LOSEIT_REFRESH_TOKEN / "
                "MCP_LOSEIT_USER_ID. Seed the container on first start."
            )
        auth.seed_from_capture(
            access_token=settings.loseit_access_token or settings.loseit_refresh_token,
            refresh_token=settings.loseit_refresh_token,
            user_id=settings.loseit_user_id,
            expires_in=settings.loseit_expires_in,
            username=settings.loseit_username,
        )
        # Immediately rotate to a real access_token if the caller only
        # supplied a refresh token.
        if not settings.loseit_access_token:
            try:
                auth.refresh()
            except Exception:  # noqa: BLE001 — surface later on first request
                pass

    return LoseItClient(auth=auth)
