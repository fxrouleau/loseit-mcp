FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /build

# Monorepo layout: copy the client library (path dependency target
# of mcp/pyproject.toml) and the MCP service.
COPY client /build/client
COPY mcp/pyproject.toml /build/mcp/pyproject.toml
COPY mcp/README.md /build/mcp/README.md
COPY mcp/src /build/mcp/src

RUN cd /build/mcp && uv sync --no-dev

# ---- runtime stage ----
FROM python:3.12-slim

RUN useradd -u 1000 -ms /bin/bash app && \
    mkdir -p /data && chown -R app:app /data

COPY --from=builder --chown=app:app /build /app

USER app
WORKDIR /app/mcp

ENV PATH="/app/mcp/.venv/bin:$PATH" \
    MCP_DATA_DIR=/data \
    MCP_BIND_HOST=0.0.0.0 \
    MCP_BIND_PORT=8787 \
    PYTHONUNBUFFERED=1

EXPOSE 8787
VOLUME ["/data"]

CMD ["python", "-m", "loseit_mcp"]
