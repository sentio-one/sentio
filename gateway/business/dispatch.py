"""Single-use HMAC dispatch authorization tokens and atomic consumption (Task B4)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any
from dataclasses import asdict

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
    recipient: str | None = None,
) -> str:
    """Issue a 30-second single-use HMAC-SHA256 dispatch authorization token."""
    if secret_key is None:
        raise BusinessProtocolError("transport_key_required")
    key = secret_key
    expires_at_ms = now_ms + DISPATCH_TOKEN_TTL_MS

    payload = {
        "business_dispatch": 1,
        "runtime_profile": plan.scope.runtime_profile,
        "contact_id": plan.scope.contact_id,
        "recipient": recipient or plan.scope.contact_id,
        "part": asdict(plan.parts[attempt.part_index]),
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
    request: dict[str, Any] | None = None,
    service: Any = None,
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

    key = secret_key or _default_transport_key(store.db_path.parent)
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

        if payload.get("operator_notice") == 1:
            _validate_operator_notice(db, payload, request, service)
        else:
            _validate_current_attempt(db, payload, request, now_ms)
        db.execute(
            "INSERT INTO business_consumed_authorizations(token_hash, attempt_id, consumed_at_ms) VALUES(?,?,?)",
            (token_hash, payload["attempt_id"], now_ms),
        )
        db.execute("UPDATE business_attempts SET state='in_flight' WHERE attempt_id=?", (payload["attempt_id"],))

    return Attempt(
        id=payload["attempt_id"],
        outbox_id=payload["outbox_id"],
        part_index=payload["part_index"],
        generation=payload["generation"],
        state="consumed",
        platform_ids=(),
        error_code=None,
    )


def _validate_current_attempt(db, payload: dict, request: dict | None, now_ms: int) -> None:
    row = db.execute(
        "SELECT a.*, o.runtime_profile, o.contact_id, o.plan_id, o.state AS outbox_state, "
        "c.context_version, c.held, c.suppressed, e.context_version AS episode_version, "
        "p.payload AS plan_payload, p.digest, p.expires_at_ms AS plan_expiry, "
        "v.expires_at_ms AS approval_expiry, g.paused "
        "FROM business_attempts a JOIN business_outbox o ON a.outbox_id=o.outbox_id "
        "JOIN business_contacts c USING(runtime_profile,transport_profile,account_id,contact_id) "
        "JOIN business_revisions r ON r.revision_id=o.revision_id "
        "JOIN business_episodes e ON e.episode_id=r.episode_id "
        "JOIN business_plans p ON p.plan_id=o.plan_id "
        "JOIN business_approvals v ON v.approval_id=o.approval_id "
        "LEFT JOIN business_governor_accounts g ON g.transport_profile=o.transport_profile AND g.account_id=o.account_id "
        "WHERE a.attempt_id=?", (payload["attempt_id"],),
    ).fetchone()
    if not row or row["state"] != "claimed" or row["outbox_state"] != "approved":
        raise BusinessConflict("attempt_not_dispatchable")
    keys = ("outbox_id", "part_index", "generation", "transport_profile", "account_id", "runtime_profile", "contact_id", "plan_id")
    if any(payload.get(k) != row[k] for k in keys):
        raise BusinessProtocolError("scope_mismatch")
    if row["held"] or row["suppressed"] or row["paused"] or row["context_version"] != row["episode_version"]:
        raise BusinessConflict("dispatch_invalidated")
    if min(row["plan_expiry"], row["approval_expiry"]) <= now_ms or payload["plan_digest"] != row["digest"]:
        raise BusinessConflict("approval_expired_or_changed")
    plan = json.loads(row["plan_payload"])
    part = plan["parts"][payload["part_index"]]
    if part != payload.get("part"):
        raise BusinessProtocolError("payload_mismatch")
    if request is None:
        return
    if request.get("recipient", payload["contact_id"]) != payload.get("recipient"):
        raise BusinessProtocolError("payload_mismatch")
    aliases = db.execute("SELECT transport_id,held FROM business_aliases WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                         (row["runtime_profile"], row["transport_profile"], row["account_id"], row["contact_id"])).fetchall()
    from gateway.whatsapp_identity import to_whatsapp_jid
    if aliases and (any(alias["held"] for alias in aliases) or payload["recipient"] not in {to_whatsapp_jid(alias["transport_id"]) for alias in aliases}):
        raise BusinessConflict("transport_target_changed")
    expected_scope = {k: row[k] for k in ("runtime_profile", "transport_profile", "account_id", "contact_id")}
    if (request.get("scope") != expected_scope or request.get("part") != part
            or any(request.get(k) != payload.get(k) for k in ("attempt_id", "plan_id", "plan_digest"))):
        raise BusinessProtocolError("payload_mismatch")
    mode = db.execute("SELECT mode FROM business_account_modes WHERE runtime_profile=? AND transport_profile=? AND account_id=?",
                      (row["runtime_profile"], row["transport_profile"], row["account_id"])).fetchone()
    reservation = db.execute("SELECT attempt_id FROM business_governor_reservations WHERE transport_profile=? AND account_id=? AND attempt_id=?",
                             (row["transport_profile"], row["account_id"], row["attempt_id"])).fetchone()
    if not mode or mode["mode"] != "supervised" or not reservation:
        raise BusinessConflict("account_not_dispatchable")


def _validate_operator_notice(db, payload: dict, request: dict | None, service: Any) -> None:
    from gateway.business.models import BusinessDenied, Scope
    from gateway.whatsapp_identity import to_whatsapp_jid
    scope = Scope(**{k: payload[k] for k in ("runtime_profile", "transport_profile", "account_id", "contact_id")})
    cfg = service.resolve_config(scope) if service else None
    if not cfg or scope.contact_id not in {to_whatsapp_jid(item) for item in cfg.operators}:
        raise BusinessDenied()
    if not request or request.get("scope") != asdict(scope) or request.get("part") != payload.get("part"):
        raise BusinessProtocolError("payload_mismatch")
    if any(request.get(k) != payload[k] for k in ("attempt_id", "plan_id", "plan_digest")):
        raise BusinessProtocolError("payload_mismatch")
    reservation = db.execute("SELECT attempt_id FROM business_governor_reservations WHERE transport_profile=? AND account_id=? AND attempt_id=? AND message_class='operator'",
                             (scope.transport_profile, scope.account_id, payload["attempt_id"])).fetchone()
    if not reservation:
        raise BusinessConflict("account_not_dispatchable")
