"""Attributable learning, lesson lifecycle, and review metrics (Task C2)."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from gateway.business.models import BusinessConflict, BusinessDenied, Scope
from gateway.business.permissions import authorize_operation
from gateway.business import store_learning

if TYPE_CHECKING:
    from gateway.business.models import Principal
    from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Lesson:
    id: str
    policy_id: str
    version: int
    text: str
    evidence_revision_ids: tuple[str, ...]
    state: str  # "proposed", "confirmed", "revoked"


def _check_profile_authorization(principal: Principal, scope: Scope) -> None:
    if principal.runtime_profile != scope.runtime_profile:
        raise BusinessDenied("cross_profile_access_denied")


def propose_lessons(
    principal: Principal,
    scope: Scope,
    *,
    policy_id: str,
    evidence_revision_ids: tuple[str, ...] = (),
    texts: tuple[str, ...] = (),
    store: BusinessStore | None = None,
) -> tuple[Lesson, ...]:
    """Propose up to five candidate lessons for human confirmation."""
    _check_profile_authorization(principal, scope)
    authorize_operation(principal, scope, "propose_lessons")

    # Enforce maximum of five proposed rules per request
    capped_texts = texts[:5]
    if not capped_texts and evidence_revision_ids:
        # Default synthesized rule if none provided
        capped_texts = (f"Rule from review evidence {evidence_revision_ids[0]}",)

    lessons = []
    now_ms = int(time.time() * 1000)

    for idx, rule_text in enumerate(capped_texts, start=1):
        lesson_id = f"lsn-{uuid.uuid4().hex[:12]}"
        lesson = Lesson(
            id=lesson_id,
            policy_id=policy_id,
            version=idx,
            text=rule_text,
            evidence_revision_ids=evidence_revision_ids,
            state="proposed",
        )
        if store is not None:
            store_learning.record_lesson(
                store,
                scope,
                lesson_id=lesson.id,
                policy_id=lesson.policy_id,
                version=lesson.version,
                text=lesson.text,
                evidence_revision_ids=lesson.evidence_revision_ids,
                state=lesson.state,
                now_ms=now_ms,
            )
        lessons.append(lesson)

    return tuple(lessons)


def confirm_lesson(
    principal: Principal,
    scope: Scope,
    lesson_id: str,
    *,
    store: BusinessStore | None = None,
) -> Lesson:
    """Confirm a proposed lesson, invalidating dependent unsent plans."""
    _check_profile_authorization(principal, scope)
    authorize_operation(principal, scope, "confirm_lesson")

    if store is None:
        raise ValueError("store required to confirm lesson")

    record = store_learning.get_lesson(store, scope, lesson_id)
    if not record:
        raise BusinessConflict("lesson_not_found")

    now_ms = int(time.time() * 1000)
    store_learning.update_lesson_state(store, scope, lesson_id, state="confirmed", now_ms=now_ms)

    return Lesson(
        id=record["lesson_id"],
        policy_id=record["policy_id"],
        version=record["version"],
        text=record["text"],
        evidence_revision_ids=record["evidence_revision_ids"],
        state="confirmed",
    )


def revoke_lesson(
    principal: Principal,
    scope: Scope,
    lesson_id: str,
    *,
    reason: str,
    store: BusinessStore | None = None,
) -> Lesson:
    """Revoke a previously confirmed or proposed lesson."""
    _check_profile_authorization(principal, scope)
    authorize_operation(principal, scope, "revoke_lesson")

    if store is None:
        raise ValueError("store required to revoke lesson")

    record = store_learning.get_lesson(store, scope, lesson_id)
    if not record:
        raise BusinessConflict("lesson_not_found")

    now_ms = int(time.time() * 1000)
    store_learning.update_lesson_state(store, scope, lesson_id, state="revoked", reason=reason, now_ms=now_ms)

    return Lesson(
        id=record["lesson_id"],
        policy_id=record["policy_id"],
        version=record["version"],
        text=record["text"],
        evidence_revision_ids=record["evidence_revision_ids"],
        state="revoked",
    )


def confirmed_lessons(
    scope: Scope,
    policy_id: str,
    *,
    store: BusinessStore | None = None,
) -> tuple[Lesson, ...]:
    """Return all active, confirmed lessons for the scope and policy."""
    if store is None:
        return ()

    rows = store_learning.list_confirmed_lessons(store, scope, policy_id)
    return tuple(
        Lesson(
            id=r["lesson_id"],
            policy_id=r["policy_id"],
            version=r["version"],
            text=r["text"],
            evidence_revision_ids=r["evidence_revision_ids"],
            state=r["state"],
        )
        for r in rows
    )


def review_metrics(
    scope: Scope,
    *,
    message_class: str,
    policy_version: str,
    model_version: str,
    store: BusinessStore | None = None,
) -> dict[str, Any]:
    """Calculate reviewer metrics strictly partitioned by revision and policy/model version."""
    if store is None:
        return {
            "reviewed": 0,
            "unavailable": 0,
            "coverage": 0.0,
            "agreement": 1.0,
            "false_approvals": 0,
            "unpaired": 0,
        }

    return store_learning.query_review_metrics(
        store,
        scope,
        message_class=message_class,
        policy_version=policy_version,
        model_version=model_version,
    )


def eligible_training_pairs(
    scope: Scope,
    *,
    policy_version: str,
    store: BusinessStore | None = None,
) -> list[dict[str, Any]]:
    """Return verified training pairs, strictly excluding manual sends and unpaired actions."""
    if store is None:
        return []
    # Queries reviewed events that are paired and agreed
    conn = store._connect()
    rows = conn.execute(
        "SELECT revision_id, outcome FROM business_reviews "
        "WHERE runtime_profile=? AND transport_profile=? AND account_id=? "
        "AND policy_version=? AND agreed=1 AND unpaired=0 AND unavailable=0",
        (scope.runtime_profile, scope.transport_profile, scope.account_id, policy_version),
    ).fetchall()
    return [{"revision_id": r["revision_id"], "outcome": r["outcome"]} for r in rows]
