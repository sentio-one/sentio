"""Single-use HMAC dispatch authorization tokens and atomic consumption (Task B4)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from hermes_cli.sqlite_util import transaction
from gateway.business.models import (
    AccountKey,
    BusinessConflict,
    BusinessProtocolError,
)
from gateway.business.outbox import Attempt
from gateway.business.render import DeliveryPlan

if TYPE_CHECKING:
    from gateway.business.store import BusinessStore

DISPATCH_TOKEN_TTL_MS = 30000  # 30 seconds


def _default_transport_key(home: Path) -> bytes:
    key_path = home / "business" / "transport.key"
    if key_path.exists():
        try:
            return key_path.read_bytes()
        except OSError:
            pass
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    # Write with 0600 permissions
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "wb") as f:
        f.write(key)
    return key


def issue_authorization(
    account: AccountKey,
    attempt: Attempt,
    plan: DeliveryPlan,
    *,
    now_ms: int,
    secret_key: bytes | None = None,
) -> str:
    """Issue a 30-second single-use HMAC-SHA256 dispatch authorization token."""
    key = secret_key or b"0" * 32
    expires_at_ms = now_ms + DISPATCH_TOKEN_TTL_MS

    payload = {
        "business_dispatch": 1,
        "transport_profile": account.transport_profile,
        "account_id": account.account_id,
        "attempt_id": attempt.id,
        "outbox_id": attempt.outbox_id,
        "part_index": attempt.part_index,
        "generation": attempt.generation,
        "plan_id": plan.id,
        "plan_digest": plan.digest,
        "expires_at_ms": expires_at_ms,
    }

    raw_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")
    signature = hmac.new(key, raw_json, hashlib.sha256).hexdigest()

    return f"{b64_payload}.{signature}"


def consume_authorization(
    token: str,
    *,
    payload_digest: str,
    now_ms: int,
    store: BusinessStore,
    secret_key: bytes | None = None,
) -> Attempt:
    """Validate, verify, and atomically consume a single-use authorization token."""
    if not token or "." not in token:
        raise BusinessProtocolError("invalid_authorization")

    b64_payload, signature = token.split(".", 1)
    # Add back padding
    pad = len(b64_payload) % 4
    if pad:
        b64_payload += "=" * (4 - pad)

    try:
        raw_json = base64.urlsafe_b64decode(b64_payload.encode("ascii"))
        payload = json.loads(raw_json.decode("utf-8"))
    except Exception as err:
        raise BusinessProtocolError("invalid_authorization") from err

    key = secret_key or b"0" * 32
    expected_sig = hmac.new(key, raw_json, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        raise BusinessProtocolError("invalid_authorization")

    if payload.get("business_dispatch") != 1:
        raise BusinessProtocolError("invalid_authorization")

    if now_ms >= payload.get("expires_at_ms", 0):
        raise BusinessProtocolError("authorization_expired")

    if payload.get("plan_digest") != payload_digest:
        raise BusinessProtocolError("digest_mismatch")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        existing = db.execute(
            "SELECT consumed_at_ms FROM business_consumed_authorizations WHERE token_hash=?",
            (token_hash,),
        ).fetchone()
        if existing:
            raise BusinessConflict("authorization_consumed")

        db.execute(
            "INSERT INTO business_consumed_authorizations(token_hash, attempt_id, consumed_at_ms) VALUES(?,?,?)",
            (token_hash, payload["attempt_id"], now_ms),
        )

    return Attempt(
        id=payload["attempt_id"],
        outbox_id=payload["outbox_id"],
        part_index=payload["part_index"],
        generation=payload["generation"],
        state="consumed",
        platform_ids=(),
        error_code=None,
    )
