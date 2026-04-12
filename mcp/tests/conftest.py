"""Shared fixtures: settings env, a mocked LoseItClient, a built Starlette
app, and a helper that walks the full OAuth flow to hand back an
already-authenticated TestClient + access token.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient


TEST_PASSWORD = "super-long-test-password-xxxxxxxxxxxxxxxxxx"
TEST_PUBLIC_URL = "https://loseit-mcp.test"
RESOURCE = TEST_PUBLIC_URL + "/mcp"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """The authorize-POST rate limiter is a module-global dict that
    survives between tests. Wipe it before every test so earlier
    requests don't bleed into later ones."""
    import loseit_mcp.oauth as oauth
    oauth._authorize_attempts.clear()
    yield
    oauth._authorize_attempts.clear()


@pytest.fixture
def fake_loseit_client() -> MagicMock:
    """A MagicMock stand-in for LoseItClient. Each test can further
    configure return values on it via `.method.return_value = ...`.
    """
    from loseit_client import FoodMeasureId, MealType

    client = MagicMock(name="LoseItClient")

    # get_day_log returns a typed-looking list by default
    client.get_day_log.return_value = []

    # search_foods / search_catalog default to empty
    client.search_foods.return_value = []
    client.search_catalog.return_value = []
    client.search_recipes.return_value = []

    return client


@pytest.fixture
def app(tmp_path, monkeypatch, fake_loseit_client):
    """Build the real Starlette app but with the LoseIt bootstrap
    replaced by our mock. Lets us exercise the full OAuth + MCP HTTP
    path without touching the network.
    """
    monkeypatch.setenv("MCP_PUBLIC_URL", TEST_PUBLIC_URL)
    monkeypatch.setenv("MCP_ADMIN_PASSWORD", TEST_PASSWORD)
    monkeypatch.setenv("MCP_DATA_DIR", str(tmp_path))
    # Bootstrap requires these even though our mock ignores them
    monkeypatch.setenv("MCP_LOSEIT_REFRESH_TOKEN", "stub")
    monkeypatch.setenv("MCP_LOSEIT_USER_ID", "1")

    # Replace the bootstrap before build_app imports it (same module lookup).
    import loseit_mcp.app as app_module
    monkeypatch.setattr(app_module, "build_client", lambda settings: fake_loseit_client)

    return app_module.build_app()


def _do_oauth_dance(client: TestClient) -> str:
    """Walk DCR → authorize → consent → token. Return the access token."""
    reg = client.post(
        "/oauth/register",
        json={
            "client_name": "Claude",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    client_id = reg.json()["client_id"]

    verifier, challenge = pkce_pair()
    r = client.get(
        "/oauth/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp",
            "state": "s",
            "resource": RESOURCE,
        },
    )
    consent = re.search(r'name="consent"\s+value="([^"]+)"', r.text).group(1)

    r = client.post(
        "/oauth/authorize",
        data={"consent": consent, "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    code = r.headers["location"].split("code=")[1].split("&")[0]

    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )
    return r.json()["access_token"]


@pytest.fixture
def authed(app):
    """Yield (TestClient, access_token) inside an active lifespan so the
    FastMCP session manager's task group is initialised."""
    with TestClient(app) as c:
        token = _do_oauth_dance(c)
        yield c, token
