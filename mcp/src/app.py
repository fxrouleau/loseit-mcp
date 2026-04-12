"""Starlette app that hosts the MCP endpoint + embedded OAuth AS.

Layout:
  /                              → health-check HTML
  /.well-known/oauth-authorization-server
  /.well-known/oauth-protected-resource
  /oauth/{register,authorize,token}
  /mcp                           → FastMCP streamable-http (tool calls)
"""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route

from .config import Settings, load_settings
from .loseit_bootstrap import build_client
from .oauth import routes as oauth_routes
from .oauth_store import OAuthStore
from .tools import register as register_tools
from .verifier import build_verifier


def _ensure_session_secret(settings: Settings) -> Settings:
    """If MCP_SESSION_SECRET wasn't set, materialise one into the data
    volume on first boot and reuse forever after. Restarts stay stable."""
    if settings.session_secret:
        return settings
    secret_file = settings.data_dir / "session_secret"
    if secret_file.exists():
        settings.session_secret = secret_file.read_text().strip()
    else:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(48)
        secret_file.write_text(generated)
        try:
            secret_file.chmod(0o600)
        except OSError:
            pass
        settings.session_secret = generated
    return settings


async def index(_request) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><title>loseit-mcp</title>"
        "<h1>loseit-mcp</h1><p>Remote MCP for LoseIt. "
        "Wire <code>/mcp</code> into claude.ai Custom Integrations.</p>"
    )


def build_app() -> Starlette:
    settings = _ensure_session_secret(load_settings())

    store = OAuthStore(settings.data_dir / "oauth.sqlite")

    # LoseIt client — wrapped as a FastMCP context, shared across calls.
    loseit = build_client(settings)

    canonical = settings.public_url.rstrip("/") + "/mcp"
    verifier = build_verifier(settings, store)

    # DNS-rebinding protection: the streamable-http transport validates
    # the Host header against an allowlist. Seed it from the configured
    # public URL plus common local dev hosts and the testserver alias.
    from urllib.parse import urlparse
    public_host = urlparse(settings.public_url).netloc
    allowed_hosts = [
        public_host,
        "localhost",
        "localhost:8787",
        "127.0.0.1",
        "127.0.0.1:8787",
        "testserver",  # Starlette's TestClient default
    ]

    mcp = FastMCP(
        "loseit",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=settings.public_url.rstrip("/"),
            resource_server_url=canonical,
            required_scopes=["mcp"],
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[settings.public_url.rstrip("/"), "https://claude.ai", "https://claude.com"],
        ),
    )
    # FastMCP defaults to routing at /mcp inside its own Starlette sub-app.
    # We're mounting at /mcp, so move the inner route to "/" so the total
    # public path stays /mcp instead of /mcp/mcp.
    mcp.settings.streamable_http_path = "/"
    register_tools(mcp, loseit)

    # FastMCP's session manager runs inside an anyio task group that is
    # only started when the app's lifespan activates. When we mount it
    # inside our own Starlette app we have to propagate that lifespan
    # manually or every request blows up with "task group not initialized".
    @asynccontextmanager
    async def lifespan(_starlette_app):
        async with mcp.session_manager.run():
            yield

    routes = [
        Route("/", index, methods=["GET"]),
        *oauth_routes(),
        # FastMCP's streamable-http ASGI app handles session IDs and
        # bearer-auth middleware internally.
        Mount("/mcp", app=mcp.streamable_http_app()),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.settings = settings
    app.state.oauth_store = store
    app.state.loseit = loseit
    return app
