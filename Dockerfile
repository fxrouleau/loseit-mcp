FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# Build under the same path the runtime stage will use. `uv sync`
# performs editable installs (mcp + client), which embed absolute
# paths into `.pth` files in the venv — if the builder and runtime
# directories differ, `import loseit_mcp` fails at startup.
WORKDIR /app

COPY client /app/client
COPY mcp/pyproject.toml /app/mcp/pyproject.toml
COPY mcp/README.md /app/mcp/README.md
COPY mcp/src /app/mcp/src

RUN cd /app/mcp && uv sync --no-dev

# ---- runtime stage ----
FROM python:3.12-slim

RUN useradd -u 1000 -ms /bin/bash app && \
    mkdir -p /data && chown -R app:app /data

COPY --from=builder --chown=app:app /app /app

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
