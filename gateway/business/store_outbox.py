"""Durable outbox storage, revision approval, and atomic attempt claims (Task B2)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from hermes_cli.sqlite_util import transaction
from gateway.business.approvals import Approval
from gateway.business.models import (
    AccountKey,
    BusinessConflict,
    BusinessDenied,
    BusinessProtocolError,
    Principal,
    Receipt,
    Scope,
)
from gateway.business.outbox import Attempt
from gateway.business.store_episodes import _scope_values


_DISPATCHED = "('awaiting_delivery','delivered','unknown')"


def _message_class(db, scope: Scope, episode_id: str) -> str:
    """Pacing class: reply to an inbound episode, follow-up to a known contact, else cold."""
    values = _scope_values(scope)
    origin = db.execute(
        "SELECT payload FROM business_ingress WHERE runtime_profile=? AND transport_profile=? AND account_id=? "
        "AND contact_id=? AND episode_id=?", (*values, episode_id)).fetchone()
    inbound_origin = bool(origin) and json.loads(origin["payload"]).get("evidence") == "inbound"
    dispatched = (
        "SELECT 1 FROM business_outbox o JOIN business_revisions r ON r.revision_id=o.revision_id "
        "WHERE o.runtime_profile=? AND o.transport_profile=? AND o.account_id=? AND o.contact_id=? "
        f"AND o.state IN {_DISPATCHED}")
    if inbound_origin:
        if not db.execute(dispatched + " AND r.episode_id=?", (*values, episode_id)).fetchone():
            return "reply"
        return "followup"
    if db.execute(dispatched, values).fetchone():
        return "followup"
    heard_from = db.execute(
        "SELECT 1 FROM business_ingress WHERE runtime_profile=? AND transport_profile=? AND account_id=? "
        "AND contact_id=? AND json_extract(payload, '$.evidence')='inbound' LIMIT 1", values).fetchone()
    return "followup" if heard_from else "cold"


def submit_revision(
    store,
    scope: Scope,
    revision_id: str,
    *,
    request_id: str,
    now_ms: int,
) -> Receipt:
    """Submit a revision to the durable outbox in held state."""
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        contact = db.execute(
            "SELECT context_version, held, suppressed FROM business_contacts "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
            _scope_values(scope),
        ).fetchone()
        if not contact:
            raise BusinessDenied()
        if contact["held"] or contact["suppressed"]:
            raise BusinessConflict("contact_held_or_suppressed")

        rev_row = db.execute(
            "SELECT episode_id, payload FROM business_revisions "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*_scope_values(scope), revision_id),
        ).fetchone()
        if not rev_row:
            raise BusinessDenied()

        rev_payload = json.loads(rev_row["payload"])
        checks = rev_payload.get("checks")
        if checks is not None and not checks.get("ok", False):
            raise BusinessProtocolError("checks_failed")

        plan_row = db.execute(
            "SELECT plan_id, expires_at_ms FROM business_plans "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*_scope_values(scope), revision_id),
        ).fetchone()
        if not plan_row:
            raise BusinessConflict("plan_not_rendered")

        plan_id = plan_row["plan_id"]
        expires_at = plan_row["expires_at_ms"]

        existing = db.execute(
            "SELECT outbox_id, state, reason FROM business_outbox "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND request_id=?",
            (*_scope_values(scope), request_id),
        ).fetchone()
        if existing:
            return Receipt(id=existing["outbox_id"], state=existing["state"], revision_id=revision_id, reason=existing["reason"])

        outbox_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO business_outbox(runtime_profile, transport_profile, account_id, contact_id, outbox_id, revision_id, plan_id, request_id, state, created_at_ms, expires_at_ms, message_class) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (*_scope_values(scope), outbox_id, revision_id, plan_id, request_id, "held", now_ms, expires_at,
             _message_class(db, scope, rev_row["episode_id"])),
        )
        return Receipt(id=outbox_id, state="held", revision_id=revision_id, reason=None)


def approve_revision(
    store,
    principal: Principal,
    scope: Scope,
    *,
    revision_id: str,
    plan_digest: str,
    now_ms: int,
    override_reason: str | None = None,
) -> Approval:
    """Authorize an exact draft revision, validating context, checks, and plan digest."""
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        contact = db.execute(
            "SELECT context_version, held, suppressed FROM business_contacts "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
            _scope_values(scope),
        ).fetchone()
        if not contact:
            raise BusinessDenied()
        if contact["held"] or contact["suppressed"]:
            raise BusinessConflict("contact_invalidated")

        rev_row = db.execute(
            "SELECT episode_id, payload FROM business_revisions "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*_scope_values(scope), revision_id),
        ).fetchone()
        if not rev_row:
            raise BusinessDenied()

        episode_id = rev_row["episode_id"]
        ep_row = db.execute(
            "SELECT context_version, state FROM business_episodes "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
            (*_scope_values(scope), episode_id),
        ).fetchone()
        if not ep_row or ep_row["context_version"] != contact["context_version"]:
            raise BusinessConflict("stale_context")

        rev_payload = json.loads(rev_row["payload"])
        checks = rev_payload.get("checks")
        if checks is not None and not checks.get("ok", False):
            raise BusinessProtocolError("checks_failed")

        review = rev_payload.get("review")
        if review is not None:
            recommendation = review.get("recommendation", "")
            confidence = float(review.get("confidence", 1.0))
            if recommendation == "reject" or confidence < 0.5:
                if not override_reason or not str(override_reason).strip():
                    raise BusinessConflict("override_reason_required")

        plan_row = db.execute(
            "SELECT plan_id, digest, expires_at_ms FROM business_plans "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*_scope_values(scope), revision_id),
        ).fetchone()
        if not plan_row:
            raise BusinessConflict("plan_not_rendered")
        if plan_row["digest"] != plan_digest:
            raise BusinessConflict("digest_mismatch")
        if plan_row["expires_at_ms"] <= now_ms:
            raise BusinessConflict("plan_expired")

        plan_id = plan_row["plan_id"]
        expires_at_ms = plan_row["expires_at_ms"]

        appr_row = db.execute(
            "SELECT approval_id, expires_at_ms, override_reason FROM business_approvals "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=? AND actor=? AND plan_digest=?",
            (*_scope_values(scope), revision_id, principal.subject, plan_digest),
        ).fetchone()
        if appr_row and appr_row["expires_at_ms"] > now_ms:
            return Approval(
                id=appr_row["approval_id"],
                plan_id=plan_id,
                revision_id=revision_id,
                actor=principal.subject,
                approved_at_ms=now_ms,
                expires_at_ms=appr_row["expires_at_ms"],
                override_reason=appr_row["override_reason"],
            )

        approval_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO business_approvals(runtime_profile, transport_profile, account_id, contact_id, approval_id, plan_id, revision_id, actor, plan_digest, approved_at_ms, expires_at_ms, override_reason, payload) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (*_scope_values(scope), approval_id, plan_id, revision_id, principal.subject, plan_digest, now_ms, expires_at_ms, override_reason, "{}"),
        )
        db.execute(
            "UPDATE business_outbox SET state='approved', approval_id=? "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=? AND state='held'",
            (approval_id, *_scope_values(scope), revision_id),
        )
        return Approval(
            id=approval_id,
            plan_id=plan_id,
            revision_id=revision_id,
            actor=principal.subject,
            approved_at_ms=now_ms,
            expires_at_ms=expires_at_ms,
            override_reason=override_reason,
        )


def reject_revision(
    store,
    principal: Principal,
    scope: Scope,
    *,
    revision_id: str,
    reason: str,
    now_ms: int,
) -> Receipt:
    """Reject a draft revision, marking its outbox items rejected."""
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        rev_row = db.execute(
            "SELECT episode_id FROM business_revisions "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*_scope_values(scope), revision_id),
        ).fetchone()
        if not rev_row:
            raise BusinessDenied()

        db.execute(
            "UPDATE business_outbox SET state='rejected', reason=? "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=? AND state IN ('held', 'approved')",
            (reason, *_scope_values(scope), revision_id),
        )
        row = db.execute(
            "SELECT outbox_id FROM business_outbox "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*_scope_values(scope), revision_id),
        ).fetchone()
        outbox_id = row["outbox_id"] if row else str(uuid.uuid4())
        return Receipt(id=outbox_id, state="rejected", revision_id=revision_id, reason=reason)


def claim_part(
    store,
    account: AccountKey,
    *,
    now_ms: int,
) -> Attempt | None:
    """Claim the next ready outbox delivery part atomically."""
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        row = db.execute(
            "SELECT o.outbox_id, o.runtime_profile, o.transport_profile, o.account_id, o.contact_id, "
            "       o.revision_id, o.plan_id, o.approval_id, "
            "       c.context_version AS contact_context_version, c.held, c.suppressed, "
            "       e.context_version AS episode_context_version, "
            "       a.expires_at_ms AS approval_expires_at, "
            "       p.payload AS plan_payload "
            "FROM business_outbox o "
            "JOIN business_contacts c ON o.runtime_profile = c.runtime_profile AND o.transport_profile = c.transport_profile AND o.account_id = c.account_id AND o.contact_id = c.contact_id "
            "JOIN business_revisions r ON o.runtime_profile = r.runtime_profile AND o.transport_profile = r.transport_profile AND o.account_id = r.account_id AND o.contact_id = r.contact_id AND o.revision_id = r.revision_id "
            "JOIN business_episodes e ON o.runtime_profile = e.runtime_profile AND o.transport_profile = e.transport_profile AND o.account_id = e.account_id AND o.contact_id = e.contact_id AND r.episode_id = e.episode_id "
            "LEFT JOIN business_approvals a ON o.approval_id = a.approval_id "
            "LEFT JOIN business_plans p ON o.plan_id = p.plan_id "
            "WHERE o.transport_profile = ? AND o.account_id = ? AND o.state = 'approved' "
            "ORDER BY o.created_at_ms ASC LIMIT 1",
            (account.transport_profile, account.account_id),
        ).fetchone()

        if not row:
            return None

        outbox_id = row["outbox_id"]
        if row["held"] or row["suppressed"]:
            db.execute("UPDATE business_outbox SET state='invalidated', reason='contact_held_or_suppressed' WHERE outbox_id=?", (outbox_id,))
            return None

        if row["contact_context_version"] != row["episode_context_version"]:
            db.execute("UPDATE business_outbox SET state='invalidated', reason='stale_context' WHERE outbox_id=?", (outbox_id,))
            return None

        if row["approval_expires_at"] is None or row["approval_expires_at"] <= now_ms:
            db.execute("UPDATE business_outbox SET state='expired', reason='approval_expired' WHERE outbox_id=?", (outbox_id,))
            return None

        plan_dict = json.loads(row["plan_payload"])
        parts = plan_dict.get("parts", [])
        for idx in range(len(parts)):
            att = db.execute(
                "SELECT attempt_id, state, generation, claimed_at_ms FROM business_attempts WHERE outbox_id=? AND part_index=? ORDER BY generation DESC LIMIT 1",
                (outbox_id, idx),
            ).fetchone()
            if not att:
                attempt_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO business_attempts(attempt_id, outbox_id, transport_profile, account_id, part_index, generation, state, platform_ids, claimed_at_ms) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (attempt_id, outbox_id, account.transport_profile, account.account_id, idx, 1, "claimed", "[]", now_ms),
                )
                return Attempt(id=attempt_id, outbox_id=outbox_id, part_index=idx, generation=1, state="claimed", platform_ids=(), error_code=None)
            if att["state"] == "scheduled" and att["claimed_at_ms"] <= now_ms:
                db.execute("UPDATE business_attempts SET state='claimed' WHERE attempt_id=?", (att["attempt_id"],))
                return Attempt(id=att["attempt_id"], outbox_id=outbox_id, part_index=idx,
                               generation=att["generation"], state="claimed", platform_ids=(), error_code=None)
            if att["state"] not in ("sent", "delivered", "accepted", "read", "played"):
                return None

        db.execute("UPDATE business_outbox SET state='awaiting_delivery' WHERE outbox_id=?", (outbox_id,))
        return None


def reschedule_attempt(store, attempt_id: str, *, not_before_ms: int, reason: str) -> None:
    """A proven unsubmitted pacing hold keeps the same attempt reusable."""
    with transaction(store._connect(), immediate=True) as db:
        db.execute("UPDATE business_attempts SET state='scheduled', claimed_at_ms=?, error_code=? "
                   "WHERE attempt_id=? AND state='claimed'", (not_before_ms, reason, attempt_id))


def update_attempt_state(
    store,
    attempt_id: str,
    *,
    state: str,
    error_code: str | None = None,
    platform_ids: tuple[str, ...] | None = None,
) -> None:
    """Update the status, platform identifiers, and error of a delivery attempt."""
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        if platform_ids is not None:
            pids = json.dumps(list(platform_ids))
            db.execute(
                "UPDATE business_attempts SET state=?, error_code=?, platform_ids=? WHERE attempt_id=?",
                (state, error_code, pids, attempt_id),
            )
        else:
            db.execute(
                "UPDATE business_attempts SET state=?, error_code=? WHERE attempt_id=?",
                (state, error_code, attempt_id),
            )

        from gateway.business.store_jobs import _refresh_outcomes
        owner = db.execute("SELECT runtime_profile FROM business_outbox o JOIN business_attempts a ON a.outbox_id=o.outbox_id WHERE a.attempt_id=?", (attempt_id,)).fetchone()
        if owner:
            _refresh_outcomes(db, owner["runtime_profile"])
    if platform_ids:
        from gateway.business.receipts import join_attempt_receipts
        join_attempt_receipts(store, attempt_id)


def get_attempt_context(store, attempt_id: str) -> dict | None:
    """Retrieve attempt details joined with its parent outbox row."""
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute(
            "SELECT a.attempt_id, a.outbox_id, a.part_index, a.generation, a.state, a.platform_ids, a.error_code, "
            "       o.runtime_profile, o.transport_profile, o.account_id, o.contact_id, o.revision_id, o.plan_id, o.message_class "
            "FROM business_attempts a "
            "JOIN business_outbox o ON a.outbox_id = o.outbox_id "
            "WHERE a.attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)


def invalidate_contact(
    store,
    scope: Scope,
    *,
    reason: str,
    now_ms: int,
) -> int:
    """Invalidate all pending outbox entries and approvals for a contact."""
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        cur = db.execute(
            "UPDATE business_outbox SET state='invalidated', reason=? "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND state IN ('held', 'approved')",
            (reason, *_scope_values(scope)),
        )
        count = cur.rowcount
        db.execute(
            "UPDATE business_contacts SET held=1 WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
            _scope_values(scope),
        )
        return count
