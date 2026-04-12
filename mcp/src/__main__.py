"""Entrypoint: `python -m loseit_mcp` → runs the uvicorn server.

For local dev you can also do `uvicorn loseit_mcp.app:build_app --factory`.
"""
from __future__ import annotations

import uvicorn

from .app import build_app
from .config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        build_app,
        factory=True,
        host=settings.bind_host,
        port=settings.bind_port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )


if __name__ == "__main__":
    main()
