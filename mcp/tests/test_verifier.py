"""Unit tests for OpaqueTokenVerifier — the bit that guards /mcp."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from loseit_mcp.oauth_store import OAuthStore
from loseit_mcp.verifier import OpaqueTokenVerifier


def _store(tmp_path: Path) -> OAuthStore:
    return OAuthStore(tmp_path / "t.sqlite")


def test_verifier_accepts_live_access_token(tmp_path):
    store = _store(tmp_path)
    # seed a client (FK constraint)
    client = store.register_client({"redirect_uris": ["x"]})
    rec = store.issue_token(
        kind="access",
        client_id=client.client_id,
        scope="mcp",
        resource="https://loseit-mcp.test/mcp",
        ttl_sec=3600,
    )
    v = OpaqueTokenVerifier(store=store, canonical_resource="https://loseit-mcp.test/mcp")
    access = asyncio.run(v.verify_token(rec.token))
    assert access is not None
    assert access.client_id == client.client_id


def test_verifier_rejects_refresh_token_presented_as_access(tmp_path):
    store = _store(tmp_path)
    client = store.register_client({"redirect_uris": ["x"]})
    refresh = store.issue_token(
        kind="refresh",
        client_id=client.client_id,
        scope="mcp",
        resource="https://loseit-mcp.test/mcp",
        ttl_sec=3600,
    )
    v = OpaqueTokenVerifier(store=store, canonical_resource="https://loseit-mcp.test/mcp")
    assert asyncio.run(v.verify_token(refresh.token)) is None


def test_verifier_rejects_expired(tmp_path):
    store = _store(tmp_path)
    client = store.register_client({"redirect_uris": ["x"]})
    rec = store.issue_token(
        kind="access",
        client_id=client.client_id,
        scope="mcp",
        resource="https://loseit-mcp.test/mcp",
        ttl_sec=-1,
    )
    v = OpaqueTokenVerifier(store=store, canonical_resource="https://loseit-mcp.test/mcp")
    assert asyncio.run(v.verify_token(rec.token)) is None


def test_verifier_rejects_wrong_audience(tmp_path):
    store = _store(tmp_path)
    client = store.register_client({"redirect_uris": ["x"]})
    rec = store.issue_token(
        kind="access",
        client_id=client.client_id,
        scope="mcp",
        resource="https://loseit-mcp.test/mcp",
        ttl_sec=3600,
    )
    v = OpaqueTokenVerifier(store=store, canonical_resource="https://evil.example/mcp")
    assert asyncio.run(v.verify_token(rec.token)) is None


def test_verifier_rejects_unknown_token(tmp_path):
    store = _store(tmp_path)
    v = OpaqueTokenVerifier(store=store, canonical_resource="https://loseit-mcp.test/mcp")
    assert asyncio.run(v.verify_token("does-not-exist")) is None
