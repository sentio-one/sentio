"""Storage helpers for business lessons and review events (Task C2)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from hermes_cli.sqlite_util import transaction
from gateway.business.models import BusinessConflict, BusinessDenied, Scope


def record_lesson(
    store,
    scope: Scope,
    *,
    lesson_id: str,
    policy_id: str,
    version: int,
    text: str,
    evidence_revision_ids: tuple[str, ...],
    state: str,
    now_ms: int,
) -> None:
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        db.execute(
            "INSERT INTO business_lessons("
            "lesson_id, runtime_profile, transport_profile, account_id, policy_id, "
            "version, text, evidence_revision_ids, state, revoke_reason, created_at_ms, updated_at_ms) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                lesson_id,
                scope.runtime_profile,
                scope.transport_profile,
                scope.account_id,
                policy_id,
                version,
                text,
                json.dumps(list(evidence_revision_ids)),
                state,
                None,
                now_ms,
                now_ms,
            ),
        )


def get_lesson(store, scope: Scope, lesson_id: str) -> dict[str, Any] | None:
    conn = store._connect()
    row = conn.execute(
        "SELECT * FROM business_lessons WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND lesson_id=?",
        (scope.runtime_profile, scope.transport_profile, scope.account_id, lesson_id),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["evidence_revision_ids"] = tuple(json.loads(d["evidence_revision_ids"]))
    return d


def update_lesson_state(
    store,
    scope: Scope,
    lesson_id: str,
    *,
    state: str,
    reason: str | None = None,
    now_ms: int,
) -> None:
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        res = db.execute(
            "UPDATE business_lessons SET state=?, revoke_reason=?, updated_at_ms=? "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND lesson_id=?",
            (
                state,
                reason,
                now_ms,
                scope.runtime_profile,
                scope.transport_profile,
                scope.account_id,
                lesson_id,
            ),
        )
        if res.rowcount == 0:
            raise BusinessConflict("lesson_not_found")


def list_confirmed_lessons(store, scope: Scope, policy_id: str) -> list[dict[str, Any]]:
    conn = store._connect()
    rows = conn.execute(
        "SELECT * FROM business_lessons "
        "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND policy_id=? AND state='confirmed' "
        "ORDER BY version ASC, created_at_ms ASC",
        (scope.runtime_profile, scope.transport_profile, scope.account_id, policy_id),
    ).fetchall()
    results = []
    for row in rows:
        d = dict(row)
        d["evidence_revision_ids"] = tuple(json.loads(d["evidence_revision_ids"]))
        results.append(d)
    return results


def record_review_event(
    store,
    scope: Scope,
    *,
    revision_id: str,
    policy_version: str,
    model_version: str,
    message_class: str,
    outcome: str,
    agreed: bool = True,
    unavailable: bool = False,
    false_approval: bool = False,
    unpaired: bool = False,
    reviewed_at_ms: int | None = None,
) -> str:
    review_id = f"revw-{uuid.uuid4().hex[:12]}"
    now_ms = int(time.time() * 1000) if reviewed_at_ms is None else reviewed_at_ms
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        db.execute(
            "INSERT INTO business_reviews("
            "review_id, runtime_profile, transport_profile, account_id, contact_id, "
            "revision_id, policy_version, model_version, message_class, outcome, "
            "agreed, false_approval, unavailable, unpaired, reviewed_at_ms) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                review_id,
                scope.runtime_profile,
                scope.transport_profile,
                scope.account_id,
                scope.contact_id,
                revision_id,
                policy_version,
                model_version,
                message_class,
                outcome,
                1 if agreed else 0,
                1 if false_approval else 0,
                1 if unavailable else 0,
                1 if unpaired else 0,
                now_ms,
            ),
        )
    return review_id


def query_review_metrics(
    store,
    scope: Scope,
    *,
    message_class: str,
    policy_version: str,
    model_version: str,
) -> dict[str, Any]:
    conn = store._connect()
    rows = conn.execute(
        "SELECT agreed, false_approval, unavailable, unpaired FROM business_reviews "
        "WHERE runtime_profile=? AND transport_profile=? AND account_id=? "
        "AND message_class=? AND policy_version=? AND model_version=?",
        (
            scope.runtime_profile,
            scope.transport_profile,
            scope.account_id,
            message_class,
            policy_version,
            model_version,
        ),
    ).fetchall()

    reviewed = len(rows)
    unavailable = sum(1 for r in rows if r["unavailable"])
    false_approvals = sum(1 for r in rows if r["false_approval"])
    unpaired = sum(1 for r in rows if r["unpaired"])
    agreed = sum(1 for r in rows if r["agreed"])

    available = reviewed - unavailable
    coverage = round(available / reviewed, 4) if reviewed > 0 else 0.0
    agreement = round(agreed / available, 4) if available > 0 else 1.0

    return {
        "reviewed": reviewed,
        "unavailable": unavailable,
        "coverage": coverage,
        "agreement": agreement,
        "false_approvals": false_approvals,
        "unpaired": unpaired,
    }
