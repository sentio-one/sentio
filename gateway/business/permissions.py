"""Authorized operator and plugin principal capabilities (Task C1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gateway.business.models import BusinessDenied

if TYPE_CHECKING:
    from gateway.business.models import Principal, Scope

OPERATOR_OPERATIONS = frozenset({
    "queue",
    "show",
    "edit",
    "approve",
    "reject",
    "tasks",
    "resolve_task",
    "pause",
    "resume",
    "digest",
    "recard",
    "reconcile",
    "status",
    "readiness",
    "set_mode",
    "shorthand",
    "reaction",
    "alert",
    "approve_digest",
})

PERMITTED_PLUGIN_OPERATIONS = frozenset({
    "submit_job",
    "submit_candidate",
    "status",
    "events",
    "ack_events",
    "record_fact",
    "record_action",
})

FORBIDDEN_PLUGIN_OPERATIONS = frozenset({
    "approve",
    "reject",
    "reconcile",
    "grant_credentials",
    "credential_grant",
    "set_mode",
})


def authorize_operation(principal: Principal, scope: Scope, operation: str) -> None:
    """Authorize an operation against principal capabilities, denying privilege escalation."""
    if principal.kind == "plugin":
        if operation in FORBIDDEN_PLUGIN_OPERATIONS or operation not in PERMITTED_PLUGIN_OPERATIONS:
            raise BusinessDenied("permission_denied")
        return

    # Human / operator principal
    if principal.kind == "human":
        ops = getattr(principal, "operations", None) or getattr(principal, "capabilities", frozenset())
        if "admin" in ops or "operator" in ops or operation in ops:
            return
        if operation in ("digest", "reaction", "shorthand", "alert", "propose_lessons", "confirm_lesson", "revoke_lesson", "credential_grant", "cutover") and ("review" in ops or "approve" in ops or "credential_grant" in ops or "operator" in ops):
            return
        raise BusinessDenied("permission_denied")

    # External or unknown principal
    raise BusinessDenied("permission_denied")
