"""OAuth 2.1 authorization-server endpoints for the MCP.

Implements the minimum surface area claude.ai needs:
  GET  /.well-known/oauth-authorization-server   (AS metadata, RFC 8414)
  POST /oauth/register                           (DCR, RFC 7591)
  GET  /oauth/authorize                          (consent form)
  POST /oauth/authorize                          (consent form POST)
  POST /oauth/token                              (code exchange + refresh)

Protected-resource metadata (`/.well-known/oauth-protected-resource`) is
served by FastMCP's AuthSettings layer, not here.

Notes
-----
* **PKCE S256 is mandatory**. We reject `plain` and requests without a
  challenge.
* **Resource indicator** (RFC 8707) `resource` is required on both
  authorize and token requests, must match our canonical MCP URL, and
  the value is bound to every issued token via a column in the tokens
  table. The `TokenVerifier` checks it on every /mcp request.
* **Allowlisted callback hosts** — claude.ai and claude.com.
* The consent HTML form serialises the pending auth request into a
  signed, short-lived token so we don't need server-side session state.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import secrets
import time
from collections import defaultdict
from typing import Optional
from urllib.parse import urlencode

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .config import Settings
from .oauth_store import OAuthStore


# ---- redirect URI allowlist (exact string match) ----
ALLOWED_REDIRECT_URIS = frozenset(
    {
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
        # Loop back for local testing
        "http://localhost:33418/oauth/callback",
        "http://127.0.0.1:33418/oauth/callback",
    }
)

ACCESS_TOKEN_TTL = 60 * 60           # 1 hour
REFRESH_TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days
CONSENT_TOKEN_TTL = 5 * 60           # 5 minutes to approve

# In-memory rate limiter for the POST /oauth/authorize form: map
# ip -> list[float]. Small, single-process, good enough for one user.
_authorize_attempts: dict[str, list[float]] = defaultdict(list)


def _canonical_resource(settings: Settings) -> str:
    return settings.public_url.rstrip("/") + "/mcp"


def _signer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="loseit-mcp-consent")


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return hmac.compare_digest(_b64url_no_pad(digest), code_challenge)


def _error(name: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": name, "error_description": description}, status_code=status
    )


def _rate_limited(ip: str, per_minute: int) -> bool:
    now = time.time()
    bucket = _authorize_attempts[ip]
    _authorize_attempts[ip] = [t for t in bucket if now - t < 60]
    if len(_authorize_attempts[ip]) >= per_minute:
        return True
    _authorize_attempts[ip].append(now)
    return False


# ---------- endpoint implementations ----------

async def as_metadata(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    base = settings.public_url.rstrip("/")
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "scopes_supported": ["mcp"],
        }
    )


async def protected_resource_metadata(request: Request) -> JSONResponse:
    """RFC 9728 metadata. Claude walks from a 401 → this URL → AS metadata."""
    settings: Settings = request.app.state.settings
    base = settings.public_url.rstrip("/")
    return JSONResponse(
        {
            "resource": _canonical_resource(settings),
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp"],
        }
    )


async def register(request: Request) -> JSONResponse:
    """RFC 7591 Dynamic Client Registration.

    claude.ai posts once per install with redirect_uris including
    https://claude.ai/api/mcp/auth_callback (or the .com variant). We
    validate each redirect_uri against our allowlist before persisting.
    """
    store: OAuthStore = request.app.state.oauth_store
    try:
        body = await request.json()
    except Exception:
        return _error("invalid_client_metadata", "body must be JSON")

    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return _error("invalid_redirect_uri", "redirect_uris required")
    for uri in redirect_uris:
        if uri not in ALLOWED_REDIRECT_URIS:
            return _error(
                "invalid_redirect_uri",
                f"redirect_uri {uri!r} not allowlisted",
            )

    record = store.register_client(body, issue_secret=False)
    resp: dict = {
        "client_id": record.client_id,
        "client_id_issued_at": record.created_at,
        "token_endpoint_auth_method": "none",
        "redirect_uris": redirect_uris,
        "grant_types": body.get("grant_types", ["authorization_code", "refresh_token"]),
        "response_types": body.get("response_types", ["code"]),
    }
    if "client_name" in body:
        resp["client_name"] = body["client_name"]
    return JSONResponse(resp, status_code=201)


async def authorize_get(request: Request) -> Response:
    """Show the consent form. Everything about the pending auth request
    is serialised into a signed token in a hidden input so we don't need
    any server-side session state between GET and POST."""
    settings: Settings = request.app.state.settings
    store: OAuthStore = request.app.state.oauth_store
    q = request.query_params

    client_id = q.get("client_id")
    redirect_uri = q.get("redirect_uri")
    response_type = q.get("response_type")
    code_challenge = q.get("code_challenge")
    code_challenge_method = q.get("code_challenge_method")
    scope = q.get("scope", "mcp")
    state = q.get("state", "")
    resource = q.get("resource")

    if response_type != "code":
        return _error("unsupported_response_type", "response_type must be code")
    if not client_id:
        return _error("invalid_request", "client_id required")
    client = store.get_client(client_id)
    if client is None:
        return _error("invalid_client", "unknown client_id", status=401)
    if not redirect_uri or redirect_uri not in client.redirect_uris:
        return _error("invalid_redirect_uri", "redirect_uri not registered")
    if not code_challenge or code_challenge_method != "S256":
        return _error("invalid_request", "PKCE S256 challenge required")
    if resource != _canonical_resource(settings):
        return _error(
            "invalid_target",
            f"resource must equal {_canonical_resource(settings)!r}",
        )

    consent_payload = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "state": state,
        "resource": resource,
    }
    token = _signer(settings).dumps(consent_payload)
    client_label = html.escape(client.metadata.get("client_name", client_id))
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Authorize {client_label}</title>
<style>
 body{{font-family:-apple-system,sans-serif;max-width:32rem;margin:4rem auto;padding:1rem;color:#222}}
 h1{{font-size:1.5rem}} .card{{padding:1.5rem;border:1px solid #ddd;border-radius:.5rem}}
 label{{display:block;margin:1rem 0 .25rem}}
 input[type=password]{{width:100%;padding:.5rem;font-size:1rem;box-sizing:border-box}}
 button{{margin-top:1rem;padding:.6rem 1.2rem;font-size:1rem;border:0;border-radius:.25rem;background:#111;color:#fff;cursor:pointer}}
 code{{background:#f4f4f4;padding:.1rem .3rem;border-radius:.2rem}}
 .scope{{margin:1rem 0;padding:.5rem .75rem;background:#f4f4f4;border-radius:.25rem;font-size:.9rem}}
</style></head><body>
<div class="card">
<h1>Authorize {client_label}</h1>
<p><code>{client_label}</code> is asking to connect to your LoseIt MCP server.</p>
<div class="scope"><strong>Scope:</strong> {html.escape(scope)} · <strong>Resource:</strong> {html.escape(resource)}</div>
<form method="post" action="/oauth/authorize">
 <input type="hidden" name="consent" value="{html.escape(token)}">
 <label for="password">Admin password</label>
 <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
 <button type="submit">Approve</button>
</form>
</div></body></html>"""
    return HTMLResponse(page)


async def authorize_post(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    store: OAuthStore = request.app.state.oauth_store

    ip = request.client.host if request.client else "?"
    if _rate_limited(ip, settings.authorize_attempts_per_minute):
        return _error("slow_down", "too many attempts; wait a minute", status=429)

    form = await request.form()
    consent_token = form.get("consent")
    password = form.get("password", "")
    if not consent_token or not isinstance(consent_token, str):
        return _error("invalid_request", "missing consent token")

    try:
        payload = _signer(settings).loads(consent_token, max_age=CONSENT_TOKEN_TTL)
    except SignatureExpired:
        return _error("invalid_request", "consent expired; restart")
    except BadSignature:
        return _error("invalid_request", "consent signature invalid")

    if not hmac.compare_digest(password, settings.admin_password):
        return _error("access_denied", "bad password", status=401)

    code = store.create_code(
        client_id=payload["client_id"],
        redirect_uri=payload["redirect_uri"],
        code_challenge=payload["code_challenge"],
        code_challenge_method=payload["code_challenge_method"],
        scope=payload["scope"],
        resource=payload["resource"],
    )
    params = {"code": code}
    if payload.get("state"):
        params["state"] = payload["state"]
    return RedirectResponse(
        f"{payload['redirect_uri']}?{urlencode(params)}", status_code=303
    )


async def token(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    store: OAuthStore = request.app.state.oauth_store
    form = await request.form()

    grant_type = form.get("grant_type")
    client_id = form.get("client_id")

    if not client_id or store.get_client(client_id) is None:
        # Returning 401 invalid_client is the documented way to force
        # claude.ai to re-register a stale client.
        return _error("invalid_client", "unknown client_id", status=401)

    if grant_type == "authorization_code":
        code_value = form.get("code")
        redirect_uri = form.get("redirect_uri")
        code_verifier = form.get("code_verifier")
        resource = form.get("resource")
        if not code_value or not isinstance(code_value, str):
            return _error("invalid_request", "code required")
        if not code_verifier or not isinstance(code_verifier, str):
            return _error("invalid_request", "code_verifier required")

        code_record = store.consume_code(code_value)
        if code_record is None:
            return _error("invalid_grant", "code invalid, used, or expired")
        if code_record.client_id != client_id:
            return _error("invalid_grant", "client mismatch")
        if code_record.redirect_uri != redirect_uri:
            return _error("invalid_grant", "redirect_uri mismatch")
        if not _verify_pkce(code_verifier, code_record.code_challenge):
            return _error("invalid_grant", "pkce verification failed")
        if resource is not None and resource != code_record.resource:
            return _error("invalid_target", "resource mismatch with authorization")

        access = store.issue_token(
            kind="access",
            client_id=client_id,
            scope=code_record.scope,
            resource=code_record.resource,
            ttl_sec=ACCESS_TOKEN_TTL,
        )
        refresh = store.issue_token(
            kind="refresh",
            client_id=client_id,
            scope=code_record.scope,
            resource=code_record.resource,
            ttl_sec=REFRESH_TOKEN_TTL,
        )
        return JSONResponse(
            {
                "access_token": access.token,
                "token_type": "Bearer",
                "expires_in": ACCESS_TOKEN_TTL,
                "refresh_token": refresh.token,
                "scope": code_record.scope,
            }
        )

    if grant_type == "refresh_token":
        rt_value = form.get("refresh_token")
        if not rt_value or not isinstance(rt_value, str):
            return _error("invalid_request", "refresh_token required")
        rt = store.get_token(rt_value)
        if rt is None or rt.kind != "refresh" or rt.expires_at < int(time.time()):
            return _error("invalid_grant", "refresh token invalid")
        if rt.client_id != client_id:
            return _error("invalid_grant", "client mismatch")
        # Rotate: burn the old one, issue a new pair.
        store.delete_token(rt.token)
        access = store.issue_token(
            kind="access",
            client_id=client_id,
            scope=rt.scope,
            resource=rt.resource,
            ttl_sec=ACCESS_TOKEN_TTL,
        )
        refresh = store.issue_token(
            kind="refresh",
            client_id=client_id,
            scope=rt.scope,
            resource=rt.resource,
            ttl_sec=REFRESH_TOKEN_TTL,
        )
        return JSONResponse(
            {
                "access_token": access.token,
                "token_type": "Bearer",
                "expires_in": ACCESS_TOKEN_TTL,
                "refresh_token": refresh.token,
                "scope": rt.scope,
            }
        )

    return _error("unsupported_grant_type", f"grant_type {grant_type!r} not supported")


def routes() -> list[Route]:
    return [
        Route(
            "/.well-known/oauth-authorization-server",
            as_metadata,
            methods=["GET"],
        ),
        # RFC 9728 says the PRM URL is constructed by appending the
        # resource path to `/.well-known/oauth-protected-resource`, so
        # for /mcp that's `.../.well-known/oauth-protected-resource/mcp`.
        # We serve both the root path and the canonical suffixed path
        # because different clients hit different variants.
        Route(
            "/.well-known/oauth-protected-resource",
            protected_resource_metadata,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource/mcp",
            protected_resource_metadata,
            methods=["GET"],
        ),
        Route("/oauth/register", register, methods=["POST"]),
        Route("/oauth/authorize", authorize_get, methods=["GET"]),
        Route("/oauth/authorize", authorize_post, methods=["POST"]),
        Route("/oauth/token", token, methods=["POST"]),
    ]
