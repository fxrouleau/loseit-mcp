FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /build

# Copy the dependency (loseit-client) first so we can resolve the local
# path dependency. The MCP repo and the client repo must be checked out
# as siblings.
COPY loseit-client /loseit-client
COPY loseit-mcp/pyproject.toml /build/pyproject.toml

# Copy source last so code changes don't bust the dep-resolution layer.
COPY loseit-mcp/src /build/src
COPY loseit-mcp/README.md /build/README.md

RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

# ---- runtime stage ----
FROM python:3.12-slim

RUN useradd -u 1000 -ms /bin/bash app && \
    mkdir -p /data && chown -R app:app /data

COPY --from=builder --chown=app:app /build /app
COPY --from=builder --chown=app:app /loseit-client /loseit-client

USER app
WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    MCP_DATA_DIR=/data \
    MCP_BIND_HOST=0.0.0.0 \
    MCP_BIND_PORT=8787 \
    PYTHONUNBUFFERED=1

EXPOSE 8787
VOLUME ["/data"]

CMD ["python", "-m", "loseit_mcp"]
