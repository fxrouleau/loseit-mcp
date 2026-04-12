"""SQLite-backed storage for the embedded OAuth 2.1 authorization server.

Three tables:

- **clients**      DCR-registered clients (one per claude.ai install + any
                   manually configured ones)
- **codes**        Short-lived authorization codes issued by /oauth/authorize
                   and consumed by /oauth/token; stores the PKCE challenge,
                   redirect URI, scope, and the resource indicator so we can
                   bind the eventual access token's audience correctly
- **tokens**       Issued access + refresh tokens, opaque random strings.
                   Rotating on refresh is implemented by deleting the old
                   refresh token and inserting a new one in the same
                   transaction.

Schema is tiny — this is a single-user server. All timestamps are unix
seconds.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    client_secret TEXT,                        -- nullable (public clients)
    metadata_json TEXT NOT NULL,               -- raw DCR registration body
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS codes (
    code TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    code_challenge_method TEXT NOT NULL,
    scope TEXT NOT NULL,
    resource TEXT,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (client_id) REFERENCES clients (client_id)
);

CREATE TABLE IF NOT EXISTS tokens (
    token TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    client_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    resource TEXT,
    expires_at INTEGER NOT NULL,
    FOREIGN KEY (client_id) REFERENCES clients (client_id)
);

CREATE INDEX IF NOT EXISTS idx_codes_expires ON codes (expires_at);
CREATE INDEX IF NOT EXISTS idx_tokens_expires ON tokens (expires_at);
"""


@dataclass
class ClientRecord:
    client_id: str
    client_secret: Optional[str]
    metadata: dict
    created_at: int

    @property
    def redirect_uris(self) -> list[str]:
        return list(self.metadata.get("redirect_uris", []))


@dataclass
class CodeRecord:
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: str
    resource: Optional[str]
    expires_at: int
    used: bool


@dataclass
class TokenRecord:
    token: str
    kind: str                 # "access" | "refresh"
    client_id: str
    scope: str
    resource: Optional[str]
    expires_at: int


class OAuthStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode = WAL")
        self._con.executescript(SCHEMA)

    # ---------- clients (DCR) ----------

    def register_client(self, metadata: dict, *, issue_secret: bool = False) -> ClientRecord:
        client_id = secrets.token_urlsafe(24)
        client_secret = secrets.token_urlsafe(32) if issue_secret else None
        now = int(time.time())
        self._con.execute(
            "INSERT INTO clients (client_id, client_secret, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (client_id, client_secret, json.dumps(metadata), now),
        )
        return ClientRecord(client_id, client_secret, metadata, now)

    def get_client(self, client_id: str) -> Optional[ClientRecord]:
        row = self._con.execute(
            "SELECT * FROM clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        if row is None:
            return None
        return ClientRecord(
            client_id=row["client_id"],
            client_secret=row["client_secret"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
        )

    # ---------- authorization codes ----------

    def create_code(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str,
        resource: Optional[str],
        ttl_sec: int = 600,
    ) -> str:
        code = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + ttl_sec
        self._con.execute(
            "INSERT INTO codes (code, client_id, redirect_uri, code_challenge, "
            "code_challenge_method, scope, resource, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                code,
                client_id,
                redirect_uri,
                code_challenge,
                code_challenge_method,
                scope,
                resource,
                expires_at,
            ),
        )
        return code

    def consume_code(self, code: str) -> Optional[CodeRecord]:
        """Atomically mark a code as used and return it. None if absent,
        already used, or expired."""
        now = int(time.time())
        row = self._con.execute(
            "SELECT * FROM codes WHERE code = ? AND used = 0 AND expires_at > ?",
            (code, now),
        ).fetchone()
        if row is None:
            return None
        self._con.execute("UPDATE codes SET used = 1 WHERE code = ?", (code,))
        return CodeRecord(
            code=row["code"],
            client_id=row["client_id"],
            redirect_uri=row["redirect_uri"],
            code_challenge=row["code_challenge"],
            code_challenge_method=row["code_challenge_method"],
            scope=row["scope"],
            resource=row["resource"],
            expires_at=row["expires_at"],
            used=True,
        )

    # ---------- tokens ----------

    def issue_token(
        self,
        *,
        kind: str,
        client_id: str,
        scope: str,
        resource: Optional[str],
        ttl_sec: int,
    ) -> TokenRecord:
        token = secrets.token_urlsafe(40)
        expires_at = int(time.time()) + ttl_sec
        self._con.execute(
            "INSERT INTO tokens (token, kind, client_id, scope, resource, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token, kind, client_id, scope, resource, expires_at),
        )
        return TokenRecord(token, kind, client_id, scope, resource, expires_at)

    def get_token(self, token: str) -> Optional[TokenRecord]:
        row = self._con.execute(
            "SELECT * FROM tokens WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            return None
        return TokenRecord(
            token=row["token"],
            kind=row["kind"],
            client_id=row["client_id"],
            scope=row["scope"],
            resource=row["resource"],
            expires_at=row["expires_at"],
        )

    def delete_token(self, token: str) -> None:
        self._con.execute("DELETE FROM tokens WHERE token = ?", (token,))

    def gc(self) -> None:
        """Drop expired codes and tokens. Cheap; call periodically."""
        now = int(time.time())
        self._con.execute("DELETE FROM codes WHERE expires_at <= ?", (now,))
        self._con.execute("DELETE FROM tokens WHERE expires_at <= ?", (now,))
