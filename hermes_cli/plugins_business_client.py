"""PluginContext.business_request handler and scoped capability proxy (Task C1)."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from gateway.business.models import BusinessDenied
from gateway.business.permissions import authorize_operation

if TYPE_CHECKING:
    from gateway.business.models import Principal, Scope

logger = logging.getLogger(__name__)


def handle_plugin_business_request(
    context_or_principal: Any,
    scope: Scope | None = None,
    *,
    operation: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a scoped, authenticated business request on behalf of a plugin."""
    from gateway.business.models import Principal, Scope
    if hasattr(context_or_principal, "manifest"):
        ctx = context_or_principal
        principal = Principal(
            subject=f"plugin:{ctx.manifest.name}",
            runtime_profile=ctx._manager.scope_key or "default",
            capabilities=frozenset({"plugin"}),
            kind="plugin",
        )
        scope = Scope(ctx._manager.scope_key or "default", "whatsapp", "default", "")
    else:
        principal = context_or_principal
    try:
        authorize_operation(principal, scope, operation)
    except BusinessDenied as exc:
        logger.debug("Plugin %s denied operation %s on %s: %s", principal.subject, operation, scope, exc)
        return {"error": "permission_denied"}

    # Mock/simulated dispatch for permitted plugin operations
    if operation == "status":
        return {"status": "ok", "scope": scope.contact_id}
    if operation == "events":
        return {"events": [], "next_cursor": 0}
    if operation == "ack_events":
        return {"acknowledged": True}
    if operation == "submit_job":
        p_dict = params or {}
        return {
            "status": "accepted",
            "state": "held",
            "message_id": None,
            "target_account": p_dict.get("account", scope.account_id),
        }
    if operation in ("submit_candidate", "record_fact", "record_action"):
        return {"status": "accepted"}

    return {"status": "ok"}
