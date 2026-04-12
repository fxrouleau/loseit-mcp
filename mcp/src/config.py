"""Runtime configuration, loaded from environment variables.

All secrets live in env — nothing on disk that isn't in a Docker volume.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCP_", env_file=".env", extra="ignore")

    # ---- public URL & hosting ----------------------------------
    public_url: str = Field(
        default="https://loseit-mcp.felixrouleau.com",
        description="Fully-qualified base URL of this server as reached from claude.ai",
    )
    bind_host: str = Field(default="0.0.0.0")
    bind_port: int = Field(default=8787)

    # ---- admin / login ----------------------------------------
    admin_password: str = Field(
        description="Single static password used to approve OAuth consent. "
        "Generate with `openssl rand -base64 96` and set as MCP_ADMIN_PASSWORD.",
    )
    session_secret: str = Field(
        default_factory=lambda: "",
        description="Secret for signing session cookies. Auto-generated on "
        "first start if unset, persisted to data_dir.",
    )

    # ---- data persistence ------------------------------------
    data_dir: Path = Field(default=Path("/data"))
    """Volume-mounted dir for tokens, signing keys, dcr client db."""

    # ---- LoseIt bootstrap ------------------------------------
    loseit_refresh_token: str | None = Field(default=None)
    loseit_access_token: str | None = Field(default=None)
    loseit_user_id: int | None = Field(default=None)
    loseit_username: str = Field(default="")
    loseit_expires_in: int = Field(default=1_209_600)

    # ---- rate limiting ---------------------------------------
    authorize_attempts_per_minute: int = Field(default=5)


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
