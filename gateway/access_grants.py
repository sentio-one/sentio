"""Invites and per-user profile grants for a centrally hosted gateway.

An admin creates an invite scoped to one profile; the invited person signs in with the company SSO and
redeems it, which records a grant for their ``(provider, issuer, sub)`` subject. ``AccessPolicy.grant_for``
merges these stored grants with the IdP-group and config grants.

* The token only *names* an invite: scope, expiry and use limits live here, and only its SHA-256 is stored.
* Every refusal is the same :class:`InviteError`, so a caller learns nothing about which invites exist.
* Redemption is one ``BEGIN IMMEDIATE`` transaction, so a single-use invite admits exactly one person even
  under concurrency.
* A person redeems a given invite at most once; if an admin revokes their grant they cannot walk back in
  with the same link. A fresh, deliberate invite restores access.
* An invite can only grant profiles, never admin.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

DEFAULT_TTL_HOURS = 24 * 7
MAX_TTL_HOURS = 24 * 30
MAX_USES = 1000
_MAX_TOKEN_LENGTH = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS invites (
    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, max_uses INTEGER NOT NULL,
    uses INTEGER NOT NULL DEFAULT 0, email TEXT, revoked_at INTEGER);
CREATE TABLE IF NOT EXISTS grants (
    subject TEXT NOT NULL, profile TEXT NOT NULL, invite_id TEXT, granted_at INTEGER NOT NULL,
    revoked_at INTEGER, PRIMARY KEY (subject, profile));
CREATE TABLE IF NOT EXISTS redemptions (
    invite_id TEXT NOT NULL, subject TEXT NOT NULL, redeemed_at INTEGER NOT NULL,
    PRIMARY KEY (invite_id, subject));
"""


class InviteError(Exception):
    """Any redemption refusal. Deliberately carries no detail about why."""

    reason = "invalid_invite"

    def __init__(self) -> None:
        super().__init__(self.reason)


@dataclass(frozen=True)
class CreatedInvite:
    invite_id: str
    token: str  # shown once; only its hash is kept
    expires_at: int


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _whole(value: object, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{what} must be an integer from {low} to {high}")
    return value


def _email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip().lower() if isinstance(value, str) else ""
    if "@" not in text or len(text) > 254:
        raise ValueError("email must look like an address")
    return text


class GrantStore:
    """SQLite-backed store. One short-lived connection per operation keeps it safe across threads."""

    def __init__(self, path: Path | str, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema_db = sqlite3.connect(self.path, timeout=10)  # executescript manages its own transaction
        try:
            schema_db.executescript(_SCHEMA)
        finally:
            schema_db.close()
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _now(self) -> int:
        return int(self._clock())

    # -- invites -------------------------------------------------------------------------------

    def create_invite(self, *, profile: str, created_by: str, ttl_hours: int = DEFAULT_TTL_HOURS,
                      max_uses: int = 1, email: Optional[str] = None) -> CreatedInvite:
        from gateway.access_policy import _profile

        name = _profile(profile)
        if name is None:
            raise ValueError("profile is not a valid profile name")
        ttl = _whole(ttl_hours, 1, MAX_TTL_HOURS, "ttl_hours")
        uses = _whole(max_uses, 1, MAX_USES, "max_uses")
        bound = _email(email)
        token, invite_id, now = secrets.token_urlsafe(32), secrets.token_hex(8), self._now()
        with self._tx() as db:
            db.execute(
                "INSERT INTO invites (id, token_hash, profile, created_by, created_at, expires_at, max_uses, email) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (invite_id, _digest(token), name, created_by, now, now + ttl * 3600, uses, bound))
        return CreatedInvite(invite_id, token, now + ttl * 3600)

    def list_invites(self) -> list[dict]:
        now = self._now()
        with self._tx() as db:
            rows = db.execute("SELECT * FROM invites ORDER BY created_at, id").fetchall()
        return [self._invite_row(row, now) for row in rows]

    @staticmethod
    def _invite_row(row: sqlite3.Row, now: int) -> dict:
        if row["revoked_at"] is not None:
            status = "revoked"
        elif row["uses"] >= row["max_uses"]:
            status = "used_up"
        elif now >= row["expires_at"]:
            status = "expired"
        else:
            status = "active"
        return {"id": row["id"], "profile": row["profile"], "created_by": row["created_by"],
                "created_at": row["created_at"], "expires_at": row["expires_at"], "max_uses": row["max_uses"],
                "uses": row["uses"], "email": row["email"], "revoked_at": row["revoked_at"], "status": status}

    def revoke_invite(self, invite_id: str, *, revoke_grants: bool = False) -> bool:
        """Stop an invite being redeemed (and, optionally, remove the access it created). False if unknown."""
        now = self._now()
        with self._tx() as db:
            if db.execute("SELECT 1 FROM invites WHERE id = ?", (invite_id,)).fetchone() is None:
                return False
            db.execute("UPDATE invites SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now, invite_id))
            if revoke_grants:
                db.execute("UPDATE grants SET revoked_at = ? WHERE invite_id = ? AND revoked_at IS NULL",
                           (now, invite_id))
        return True

    # -- redemption ----------------------------------------------------------------------------

    def redeem(self, token: str, *, subject: str, email: Optional[str] = None) -> str:
        """The profile now granted to *subject*. Raises :class:`InviteError` for every refusal."""
        return self.redeem_with_id(token, subject=subject, email=email)[0]

    def redeem_with_id(self, token: str, *, subject: str, email: Optional[str] = None) -> tuple[str, str]:
        if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LENGTH or not subject:
            raise InviteError()
        now = self._now()
        with self._tx() as db:
            row = db.execute("SELECT * FROM invites WHERE token_hash = ?", (_digest(token),)).fetchone()
            if row is None or row["revoked_at"] is not None or now >= row["expires_at"]:
                raise InviteError()
            if row["email"] and (email or "").strip().lower() != row["email"]:
                raise InviteError()
            prior = db.execute("SELECT 1 FROM redemptions WHERE invite_id = ? AND subject = ?",
                               (row["id"], subject)).fetchone()
            if prior is not None:
                # Clicking again is harmless while the access stands; a revoked person stays out.
                active = db.execute("SELECT 1 FROM grants WHERE subject = ? AND profile = ? AND revoked_at IS NULL",
                                    (subject, row["profile"])).fetchone()
                if active is None:
                    raise InviteError()
                return row["profile"], row["id"]
            if row["uses"] >= row["max_uses"]:
                raise InviteError()
            db.execute("INSERT INTO redemptions (invite_id, subject, redeemed_at) VALUES (?, ?, ?)",
                       (row["id"], subject, now))
            db.execute(
                "INSERT INTO grants (subject, profile, invite_id, granted_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(subject, profile) DO UPDATE SET revoked_at = NULL, invite_id = excluded.invite_id, "
                "granted_at = excluded.granted_at", (subject, row["profile"], row["id"], now))
            db.execute("UPDATE invites SET uses = uses + 1 WHERE id = ?", (row["id"],))
            return row["profile"], row["id"]

    # -- grants --------------------------------------------------------------------------------

    def profiles_for(self, subject: str) -> frozenset:
        with self._tx() as db:
            rows = db.execute("SELECT profile FROM grants WHERE subject = ? AND revoked_at IS NULL",
                              (subject,)).fetchall()
        return frozenset(row["profile"] for row in rows)

    def list_grants(self) -> list[dict]:
        with self._tx() as db:
            rows = db.execute("SELECT subject, profile, invite_id, granted_at FROM grants "
                              "WHERE revoked_at IS NULL ORDER BY granted_at, subject, profile").fetchall()
        return [dict(row) for row in rows]

    def revoke_grant(self, subject: str, profile: str) -> bool:
        with self._tx() as db:
            changed = db.execute("UPDATE grants SET revoked_at = ? WHERE subject = ? AND profile = ? "
                                 "AND revoked_at IS NULL", (self._now(), subject, profile)).rowcount
        return changed > 0


_stores: dict[str, GrantStore] = {}


def default_store() -> GrantStore:
    """The launch profile's store (access policy is host-level, owned by the launch scope)."""
    from hermes_constants import get_process_hermes_home

    path = Path(get_process_hermes_home()) / "access_grants.db"
    key = str(path)
    if key not in _stores:
        _stores[key] = GrantStore(path)
    return _stores[key]
