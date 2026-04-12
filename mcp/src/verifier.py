"""Token verifier used by FastMCP to check bearer tokens on /mcp.

Validates:
 * token exists and is marked as access
 * not expired
 * audience (resource) matches our canonical MCP URL

Returns an `AccessToken` on success so FastMCP can attach it to the
request context. Anything else → returns None → FastMCP responds with
401 + WWW-Authenticate pointing at the PRM endpoint.
"""
from __future__ import annotations

import time
from typing import Optional

from mcp.server.auth.provider import AccessToken, TokenVerifier

from .config import Settings
from .oauth_store import OAuthStore


class OpaqueTokenVerifier(TokenVerifier):
    def __init__(self, *, store: OAuthStore, canonical_resource: str) -> None:
        self._store = store
        self._resource = canonical_resource

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        record = self._store.get_token(token)
        if record is None:
            return None
        if record.kind != "access":
            return None
        if record.expires_at < int(time.time()):
            return None
        if record.resource and record.resource != self._resource:
            return None
        return AccessToken(
            token=record.token,
            client_id=record.client_id,
            scopes=record.scope.split() if record.scope else ["mcp"],
            expires_at=record.expires_at,
            resource=record.resource,
        )


def build_verifier(settings: Settings, store: OAuthStore) -> OpaqueTokenVerifier:
    canonical = settings.public_url.rstrip("/") + "/mcp"
    return OpaqueTokenVerifier(store=store, canonical_resource=canonical)
