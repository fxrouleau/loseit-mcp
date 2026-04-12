# loseit-mcp

A remote MCP server that wraps [`loseit-client`](../loseit-client) for use
with **claude.ai Custom Integrations**. Self-hosted, single-user, Docker.
Implements the full MCP 2025-06-18 spec including an embedded OAuth 2.1
authorization server with Dynamic Client Registration, PKCE, and
resource-indicator-bound tokens.

## What it exposes to Claude

13 tools, all backed by `loseit-client`:

- `list_units`
- `get_day_log(date?)`
- `search_foods(query, limit?)` — user's personal food library
- `search_catalog(query, limit?)` — full LoseIt catalog
- `search_recipes(query, limit?)`
- `barcode_lookup(barcode)`
- `log_food(food_uuid, meal, quantity?, measure?)`
- `log_food_from_barcode(barcode, meal, quantity?, serving_index?)`
- `log_calories(name, calories, meal, fat_g?, carbohydrate_g?, protein_g?)`
- `edit_log_entry(entry_uuid, food_uuid, meal, quantity?)`
- `delete_log_entry(entry_uuid, food_uuid, food_name, meal, calories, …)`
- `create_recipe(name, ingredients, total_servings?)`
- `delete_recipe(recipe_uuid, recipe_name)`

## Endpoints

| Path                                               | Purpose                                        |
|----------------------------------------------------|------------------------------------------------|
| `GET /`                                            | Health-check HTML                              |
| `GET /.well-known/oauth-authorization-server`      | RFC 8414 AS metadata                           |
| `GET /.well-known/oauth-protected-resource`        | RFC 9728 PRM (root alias)                      |
| `GET /.well-known/oauth-protected-resource/mcp`    | RFC 9728 PRM (canonical suffixed path)         |
| `POST /oauth/register`                             | RFC 7591 Dynamic Client Registration           |
| `GET /oauth/authorize`                             | Consent form (HTML)                            |
| `POST /oauth/authorize`                            | Consent form submit → auth code                |
| `POST /oauth/token`                                | Code exchange + refresh-token rotation         |
| `POST /mcp`                                        | MCP streamable-HTTP endpoint (bearer auth)     |

## Security model

- **OAuth 2.1** with PKCE S256 mandatory; authorization codes are
  single-use, 10-minute TTL; access tokens are opaque random strings,
  1-hour TTL; refresh tokens are rotated on every use.
- **Resource indicator (RFC 8707)** — claude.ai sends
  `resource=https://<public-url>/mcp` on authorize and token requests.
  The canonical resource is bound to every issued token via the
  `tokens.resource` column and revalidated by the `TokenVerifier` on
  every `/mcp` request.
- **Redirect URI allowlist** is strict and checked at DCR time:
  `https://claude.ai/api/mcp/auth_callback`,
  `https://claude.com/api/mcp/auth_callback`, plus loopback for local
  development. Anything else is rejected with `invalid_redirect_uri`.
- **Consent form** is gated by a single static password from
  `MCP_ADMIN_PASSWORD`. Pending auth requests are serialised into a
  signed `itsdangerous` token in a hidden field, so there's no
  server-side session state and the consent form can't be replayed
  past 5 minutes.
- **Rate limit**: 5 POSTs per IP per minute on the consent endpoint.
- **DNS rebinding protection**: FastMCP's `TransportSecuritySettings`
  allowlists the configured public hostname for `Host` and `Origin`
  headers on `/mcp`.

## Environment variables

All prefixed with `MCP_`:

| Var                          | Required | Purpose                                                 |
|------------------------------|----------|---------------------------------------------------------|
| `MCP_PUBLIC_URL`             | ✅       | Canonical public URL; used in discovery + token audience |
| `MCP_ADMIN_PASSWORD`         | ✅       | Password for the consent form                           |
| `MCP_BIND_HOST`              | —        | Default `0.0.0.0`                                       |
| `MCP_BIND_PORT`              | —        | Default `8787`                                          |
| `MCP_DATA_DIR`               | —        | Default `/data` (volume)                                |
| `MCP_SESSION_SECRET`         | —        | Auto-generated on first boot if unset                   |
| `MCP_LOSEIT_REFRESH_TOKEN`   | ✅ first boot | Seed the LoseIt auth path                          |
| `MCP_LOSEIT_ACCESS_TOKEN`    | —        | Optional, skips first refresh                           |
| `MCP_LOSEIT_USER_ID`         | ✅ first boot | LoseIt user id (from the captured login JSON)      |
| `MCP_LOSEIT_USERNAME`        | —        | Cosmetic                                                |

## Deployment: VPS + Portainer + existing Caddy

You already have Caddy running on the VPS terminating TLS via the
Cloudflare DNS-01 module. The MCP container doesn't need its own TLS;
Caddy terminates and reverse-proxies over the docker network.

### 1. Generate the admin password

```bash
openssl rand -base64 96 | tr -d '\n' > /tmp/mcp-password
cat /tmp/mcp-password   # copy, you'll need it twice
```

### 2. Deploy the stack in Portainer

In Portainer → **Stacks** → **Add stack** → name `loseit-mcp` →
**Repository** (or paste the contents of `compose.yml`). Set environment
variables in the stack UI:

```
MCP_PUBLIC_URL=https://loseit-mcp.felixrouleau.com
MCP_ADMIN_PASSWORD=<the 128 char token you generated>
MCP_LOSEIT_REFRESH_TOKEN=<from the captured login response>
MCP_LOSEIT_USER_ID=<e.g. 34641935>
MCP_LOSEIT_USERNAME=<your email>
TZ=America/Toronto
```

Point the stack's build context at wherever you've cloned the repos on
the VPS. The Dockerfile expects `loseit-client/` and `loseit-mcp/` to be
siblings — the build context is `..` relative to the Dockerfile.

### 3. Expose via Caddy

Add to your Caddyfile (or Caddy JSON config):

```caddy
loseit-mcp.felixrouleau.com {
    tls {
        dns cloudflare {env.CF_API_TOKEN}
    }
    reverse_proxy loseit-mcp:8787
}
```

Both Caddy and the `loseit-mcp` container need to share a docker network
so the `loseit-mcp:8787` hostname resolves. Either put them in the same
Portainer stack or attach them to a pre-existing external network
(e.g. `caddy_net`).

After reloading Caddy, confirm the discovery endpoint:

```bash
curl -sS https://loseit-mcp.felixrouleau.com/.well-known/oauth-authorization-server | jq .
```

### 4. Wire it into claude.ai

1. https://claude.ai → Settings → Integrations → **Add custom integration**.
2. Name: `loseit`.
3. URL: `https://loseit-mcp.felixrouleau.com/mcp`.
4. Click Connect. Claude will:
   - Fetch the AS metadata from `/.well-known/oauth-authorization-server`.
   - POST to `/oauth/register` (Dynamic Client Registration).
   - Open the `/oauth/authorize` consent page in a new tab.
5. Enter the `MCP_ADMIN_PASSWORD` and click **Approve**. You're redirected
   back to claude.ai with an auth code. Claude exchanges it for a token
   and the integration turns green.

Subsequent access tokens rotate silently via `/oauth/token` with
`grant_type=refresh_token`. The consent form only reappears if the
refresh token ever expires or you revoke it by blowing away `/data/oauth.sqlite`
on the container.

## Local development

```bash
git clone loseit-client loseit-mcp          # as siblings
cd loseit-mcp
cp .env.example .env                        # fill in the three required vars
uv sync
uv run python -m loseit_mcp                 # http://localhost:8787
```

Tests:

```bash
uv run pytest
```

The test suite exercises DCR → authorize → consent → token → refresh
rotation, plus bad-password and bad-redirect-URI rejection paths. The
full MCP initialize + tools/list happy path is covered by an ad-hoc
script in `tests/` (TODO promote to pytest).

## Known limitations

- One user, one password. If you need multi-tenancy wire in an
  upstream IdP and verify tokens downstream.
- No observability beyond uvicorn access logs.
- The LoseIt refresh token lives in the data volume; a VPS compromise
  plus the mount gives an attacker your LoseIt account.
- FastMCP's streamable-http transport insists on a trailing slash on
  `/mcp/`; the server 307-redirects `/mcp` → `/mcp/` and all HTTP
  clients including claude.ai handle that transparently.
