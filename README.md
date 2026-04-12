# loseit

Personal monorepo for a LoseIt automation stack.

- **`client/`** — `loseit-client`, an unofficial Python client for LoseIt's
  private sync API. Reverse-engineered from the Android app. Handles auth,
  transaction bundles, reads, barcode lookup, catalog search, recipe CRUD.
  See `client/README.md`.
- **`mcp/`** — `loseit-mcp`, a remote MCP server that wraps the client for
  use with **claude.ai Custom Integrations**. OAuth 2.1 authorization
  server with DCR, PKCE, resource indicators, bearer-token-protected
  streamable-HTTP `/mcp` endpoint. See `mcp/README.md`.

## Quick start (local dev)

```bash
# Client on its own
cd client
uv sync
uv run pytest

# MCP service (pulls client from the sibling path)
cd ../mcp
uv sync
export MCP_ADMIN_PASSWORD=$(openssl rand -base64 96 | tr -d '\n')
export MCP_LOSEIT_REFRESH_TOKEN=... MCP_LOSEIT_USER_ID=...
uv run python -m loseit_mcp
# → http://localhost:8787/mcp
```

## Deploy (Docker / Portainer)

Single `Dockerfile` and `compose.yml` at the root build both in one go.
The Dockerfile copies `client/` and `mcp/` into the image, `uv sync`s
the MCP's deps (which pulls the client via its path dep), and runs
`python -m loseit_mcp`.

```bash
docker compose up -d --build
```

For Portainer + Caddy deployment guide and the claude.ai wiring steps,
see `mcp/README.md`.

## Tests

Both packages have their own test suites:

```bash
(cd client && uv run pytest -q)   # 30 passing
(cd mcp    && uv run pytest -q)   # 18 passing
```
