"""LoseIt authentication.

Flow:
  1. Initial login (`POST /account/login`) requires a valid reCAPTCHA token,
     so it can't be fully automated. The user performs this once via the
     app/browser; we capture the returned access_token + refresh_token.
  2. Subsequent refreshes (`POST /auth/token`) do NOT require captcha. The
     client keeps rotating the pair and persists it to disk.

Token cache format: JSON file, mode 0600, at
`~/.loseit_client/tokens.json`.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import requests

SYNC_HOST = "https://sync.loseit.com"
DEFAULT_CACHE = Path.home() / ".loseit_client" / "tokens.json"
# Reuse the native app's user-agent and static headers from our captures so
# the request looks identical to a real app login.
DEFAULT_HEADERS = {
    "user-agent": "LoseIt!/18.2.300 (Android 16; emu64xa)",
    "x-loseit-version": "20.2.300",
    "x-loseit-device-type": "Android",
    "x-loseit-hoursfromgmt": "-4",
    "accept-encoding": "gzip",
}
REFRESH_MARGIN_SEC = 60 * 60 * 24  # refresh when <24h remains


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    user_id: int
    expires_at: float         # unix seconds
    username: str = ""
    device_id: str = ""

    def dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Tokens":
        return cls(**d)


def _default_device_id() -> str:
    # Any UUID works; the server just needs something stable per install.
    import uuid
    return f"DROID-UID-{uuid.uuid4().hex.upper()}"


class TokenStore:
    def __init__(self, path: Path = DEFAULT_CACHE) -> None:
        self.path = path

    def load(self) -> Optional[Tokens]:
        if not self.path.exists():
            return None
        try:
            return Tokens.from_dict(json.loads(self.path.read_text()))
        except Exception:
            return None

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(tokens.dict(), indent=2))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


class Auth:
    def __init__(
        self,
        *,
        store: TokenStore | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.store = store or TokenStore()
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._tokens: Optional[Tokens] = self.store.load()

    @property
    def tokens(self) -> Optional[Tokens]:
        return self._tokens

    def login_with_password(
        self,
        *,
        username: str,
        password: str,
        captcha_token: str,
        device_id: str | None = None,
    ) -> Tokens:
        """Initial login. Requires a live reCAPTCHA token — get one by
        solving the challenge in a browser with site key
        6LfuWNElAAAAAH1QvfazO3qTevBrGdBuAlYTcBdM, or by submitting the app's
        login form once and capturing the POST body.
        """
        device = device_id or _default_device_id()
        headers = dict(DEFAULT_HEADERS)
        headers["x-fitnow-deviceid"] = device
        headers["content-type"] = "application/x-www-form-urlencoded"
        data = {
            "username": username,
            "password": password,
            "captcha_token": captcha_token,
            "captcha_site_key": "6LfuWNElAAAAAH1QvfazO3qTevBrGdBuAlYTcBdM",
            "grant_type": "password",
        }
        r = self.session.post(f"{SYNC_HOST}/account/login", headers=headers, data=data)
        r.raise_for_status()
        body = r.json()
        t = Tokens(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            user_id=int(body["user_id"]),
            expires_at=time.time() + float(body["expires_in"]),
            username=body.get("username", username),
            device_id=device,
        )
        self.store.save(t)
        self._tokens = t
        return t

    def seed_from_capture(
        self,
        *,
        access_token: str,
        refresh_token: str,
        user_id: int,
        expires_in: int,
        username: str = "",
        device_id: str | None = None,
    ) -> Tokens:
        """Seed tokens captured from the real app's login response (no
        captcha dance). Useful for bootstrapping the client once."""
        t = Tokens(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=int(user_id),
            expires_at=time.time() + float(expires_in),
            username=username,
            device_id=device_id or _default_device_id(),
        )
        self.store.save(t)
        self._tokens = t
        return t

    def refresh(self) -> Tokens:
        """Rotate the access token via /auth/token. No captcha required."""
        if self._tokens is None:
            raise RuntimeError("no tokens loaded; run login_with_password() or seed_from_capture() first")
        headers = dict(DEFAULT_HEADERS)
        headers["x-fitnow-deviceid"] = self._tokens.device_id or _default_device_id()
        headers["content-type"] = "application/x-www-form-urlencoded"
        data = {
            "refresh_token": self._tokens.refresh_token,
            "access_token": self._tokens.access_token,
            "grant_type": "refresh_token",
        }
        r = self.session.post(f"{SYNC_HOST}/auth/token", headers=headers, data=data)
        r.raise_for_status()
        body = r.json()
        t = Tokens(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", self._tokens.refresh_token),
            user_id=self._tokens.user_id,
            expires_at=time.time() + float(body.get("expires_in", 1209600)),
            username=self._tokens.username,
            device_id=self._tokens.device_id,
        )
        self.store.save(t)
        self._tokens = t
        return t

    def ensure_fresh(self) -> Tokens:
        """Return a valid access token, refreshing if we're within the
        safety margin of expiry."""
        if self._tokens is None:
            raise RuntimeError("not authenticated")
        if self._tokens.expires_at - time.time() < REFRESH_MARGIN_SEC:
            self.refresh()
        return self._tokens
