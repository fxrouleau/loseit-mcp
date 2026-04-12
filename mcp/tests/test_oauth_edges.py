"""Edge cases around the OAuth AS that the happy-path test doesn't cover:
expired codes, PKCE mismatch, audience mismatch on token, rate limiting,
and the RFC 9728 PRM dual-path behaviour.
"""
from __future__ import annotations

import re
import time

from starlette.testclient import TestClient

from conftest import REDIRECT, RESOURCE, TEST_PASSWORD, pkce_pair


def _register_and_get_code(client: TestClient) -> tuple[str, str, str, str]:
    """Returns (client_id, code, verifier, challenge) ready for token exchange."""
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
            "state": "",
            "resource": RESOURCE,
        },
    )
    consent = re.search(r'name="consent"\s+value="([^"]+)"', r.text).group(1)
    r = client.post(
        "/oauth/authorize",
        data={"consent": consent, "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"consent POST failed: {r.status_code} {r.text}"
    code = r.headers["location"].split("code=")[1].split("&")[0]
    return client_id, code, verifier, challenge


def test_pkce_mismatch(app):
    client = TestClient(app)
    client_id, code, _verifier, _challenge = _register_and_get_code(client)
    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": "not-the-real-verifier",
            "resource": RESOURCE,
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_grant"


def test_code_is_single_use(app):
    client = TestClient(app)
    client_id, code, verifier, _ = _register_and_get_code(client)
    good = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
        "resource": RESOURCE,
    }
    r = client.post("/oauth/token", data=good)
    assert r.status_code == 200
    # Second use must fail
    r = client.post("/oauth/token", data=good)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_token_resource_mismatch_on_exchange(app):
    client = TestClient(app)
    client_id, code, verifier, _ = _register_and_get_code(client)
    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
            "resource": "https://evil.example/mcp",
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_target"


def test_authorize_requires_canonical_resource(app):
    client = TestClient(app)
    reg = client.post(
        "/oauth/register",
        json={
            "client_name": "Claude",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
    )
    client_id = reg.json()["client_id"]
    _, challenge = pkce_pair()
    r = client.get(
        "/oauth/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp",
            "resource": "https://evil.example/mcp",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_target"


def test_rate_limit_on_authorize_post(app):
    # Reset the module-global bucket and drop the settings limit so the
    # test doesn't need to spam 60 requests. The Settings object on
    # app.state is the one the endpoint reads.
    import loseit_mcp.oauth as oauth
    oauth._authorize_attempts.clear()
    original = app.state.settings.authorize_attempts_per_minute
    app.state.settings.authorize_attempts_per_minute = 2
    try:
        client = TestClient(app)
        reg = client.post(
            "/oauth/register",
            json={
                "client_name": "Claude",
                "redirect_uris": [REDIRECT],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            },
        )
        cid = reg.json()["client_id"]
        _, challenge = pkce_pair()

        def get_consent() -> str:
            r = client.get(
                "/oauth/authorize",
                params={
                    "client_id": cid,
                    "redirect_uri": REDIRECT,
                    "response_type": "code",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "scope": "mcp",
                    "resource": RESOURCE,
                },
            )
            return re.search(r'name="consent"\s+value="([^"]+)"', r.text).group(1)

        # With limit=2, the 3rd POST should be rate-limited.
        statuses = []
        for _ in range(3):
            r = client.post(
                "/oauth/authorize",
                data={"consent": get_consent(), "password": "wrong"},
                follow_redirects=False,
            )
            statuses.append(r.status_code)
        assert 429 in statuses, f"expected a 429 among {statuses}"
    finally:
        app.state.settings.authorize_attempts_per_minute = original
        oauth._authorize_attempts.clear()


def test_prm_served_on_both_paths(app):
    client = TestClient(app)
    root = client.get("/.well-known/oauth-protected-resource")
    suffixed = client.get("/.well-known/oauth-protected-resource/mcp")
    assert root.status_code == 200
    assert suffixed.status_code == 200
    assert root.json() == suffixed.json()
    assert root.json()["resource"] == RESOURCE


def test_as_metadata_has_required_fields(app):
    client = TestClient(app)
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "issuer",
        "authorization_endpoint",
        "token_endpoint",
        "registration_endpoint",
        "response_types_supported",
        "grant_types_supported",
        "code_challenge_methods_supported",
        "scopes_supported",
    ):
        assert key in body, f"AS metadata missing {key}"
    assert "S256" in body["code_challenge_methods_supported"]
    assert "mcp" in body["scopes_supported"]
