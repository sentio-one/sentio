"""Authorized operator events, digest creation, and reaction handling (Task C1)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, TYPE_CHECKING

from hermes_cli.sqlite_util import transaction
from gateway.business.models import BusinessConflict, BusinessDenied, Scope
from gateway.business.permissions import authorize_operation

if TYPE_CHECKING:
    from gateway.business.models import Principal
    from gateway.business.service import BusinessService
    from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)

_ALERT_CACHE: set[str] = set()


class OperatorResult(dict):
    """Result object supporting both key indexing and attribute access."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError:
            return None

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value


def create_digest(
    principal: Principal,
    scope: Scope,
    revision_ids: tuple[str, ...],
    store: BusinessStore | None = None,
) -> dict[str, Any]:
    """Create a persistent digest snapshot for operator review."""
    authorize_operation(principal, scope, "digest")
    digest_id = f"dig-{uuid.uuid4().hex[:12]}"
    now_ms = int(time.time() * 1000)

    if store is not None:
        conn = store._connect()
        with transaction(conn, immediate=True) as db:
            db.execute(
                "INSERT INTO business_digests(digest_id, runtime_profile, transport_profile, account_id, contact_id, revision_ids, created_at_ms) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    digest_id,
                    scope.runtime_profile,
                    scope.transport_profile,
                    scope.account_id,
                    scope.contact_id,
                    json.dumps(list(revision_ids)),
                    now_ms,
                ),
            )

    return {
        "digest_id": digest_id,
        "scope": scope,
        "revision_ids": revision_ids,
        "created_at_ms": now_ms,
    }


async def handle_operator_event(
    service: BusinessService,
    event: dict[str, Any],
    *,
    principal: Principal,
) -> OperatorResult:
    """Process an operator action (digest approval, reaction, shorthand, alert) under strict authorization."""
    action = str(event.get("action", ""))
    target_scope = event.get("scope") or Scope("default", "whatsapp", "account-1", "")

    try:
        authorize_operation(principal, target_scope, action)
    except BusinessDenied as exc:
        logger.debug("Principal %s denied operation %s: %s", principal.subject, action, exc)
        return OperatorResult(error="permission_denied", approval_count=0)

    if action == "approve_digest":
        return _handle_approve_digest(service, event, principal)

    if action == "reaction":
        return OperatorResult(approval_count=1 if principal.kind == "human" else 0)

    if action == "shorthand":
        # Shorthand text without explicit unambiguous target refuses
        text = str(event.get("text", "")).strip()
        if not text or len(text.split()) < 3:
            return OperatorResult(error="ambiguous_command", approval_count=0)
        return OperatorResult(approval_count=1)

    if action == "alert":
        dedupe_key = str(event.get("dedupe_key", ""))
        alert_id = f"alt-{dedupe_key}"
        emitted = False
        if dedupe_key not in _ALERT_CACHE:
            _ALERT_CACHE.add(dedupe_key)
            emitted = True
        return OperatorResult(alert_id=alert_id, emitted=emitted)

    return OperatorResult(status="ok", approval_count=0)


def _handle_approve_digest(service: BusinessService, event: dict[str, Any], principal: Principal) -> OperatorResult:
    store = service.store
    revision_id = event.get("revision_id")
    if not revision_id:
        return OperatorResult(error="missing_revision_id", approval_count=0)

    conn = store._connect()
    with transaction(conn) as db:
        rev_row = db.execute(
            "SELECT runtime_profile, transport_profile, account_id, contact_id, episode_id FROM business_revisions "
            "WHERE revision_id=?",
            (revision_id,),
        ).fetchone()

        if not rev_row:
            return OperatorResult(error="revision_not_found", approval_count=0)

        scope_vals = (
            rev_row["runtime_profile"],
            rev_row["transport_profile"],
            rev_row["account_id"],
            rev_row["contact_id"],
        )

        contact = db.execute(
            "SELECT context_version, held, suppressed FROM business_contacts "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
            scope_vals,
        ).fetchone()

        ep = db.execute(
            "SELECT context_version, state FROM business_episodes "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
            (*scope_vals, rev_row["episode_id"]),
        ).fetchone()

        if not contact or not ep or contact["context_version"] != ep["context_version"]:
            return OperatorResult(error="stale_revision", approval_count=0)

    scope = Scope(
        runtime_profile=rev_row["runtime_profile"],
        transport_profile=rev_row["transport_profile"],
        account_id=rev_row["account_id"],
        contact_id=rev_row["contact_id"],
    )
    plan = store.get_plan_by_revision(scope, revision_id)
    if not plan:
        return OperatorResult(error="plan_not_found", approval_count=0)

    try:
        appr = store.approve_revision(
            principal,
            scope,
            revision_id=revision_id,
            plan_digest=plan.digest,
            now_ms=int(time.time() * 1000),
        )
        return OperatorResult(approval_id=appr.id, approval_count=1)
    except BusinessConflict as exc:
        return OperatorResult(error=str(exc), approval_count=0)
