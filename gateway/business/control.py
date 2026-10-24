"""Local control protocol verbs and ACL enforcement for business requests (Task B4)."""

from __future__ import annotations

import logging
from typing import Any

from gateway.business.dispatch import consume_authorization
from gateway.business.models import (
    AccountKey,
    BusinessConflict,
    BusinessDenied,
    BusinessProtocolError,
    Principal,
    Scope,
)
from gateway.business.service import BusinessService

logger = logging.getLogger(__name__)


def handle_business_request(
    service: BusinessService,
    *,
    operation: str,
    params: dict[str, Any],
    principal: Principal | None,
) -> dict[str, Any]:
    """Execute authenticated business operations over local control socket/pipe."""
    if principal is None:
        return {"ok": False, "error": "permission_denied"}

    try:
        if operation == "ping":
            return {"ok": True, "pong": True}

        if operation == "business.consume":
            token = params.get("token", "")
            payload_digest = params.get("payload_digest", "")
            now_ms = params.get("now_ms", 0)
            attempt = consume_authorization(token, payload_digest=payload_digest, now_ms=now_ms, store=service.store)
            return {"ok": True, "attempt_id": attempt.id, "state": attempt.state}

        if operation == "business.invalidate":
            scope = Scope(**params["scope"])
            reason = params.get("reason", "manual_invalidation")
            now_ms = params.get("now_ms")
            count = service.store.invalidate_contact(scope, reason=reason, now_ms=now_ms or 0)
            return {"ok": True, "count": count}

        return {"ok": False, "error": "unknown_operation"}

    except BusinessProtocolError as err:
        return {"ok": False, "error": str(err.code)}
    except BusinessConflict as err:
        return {"ok": False, "error": str(err.code)}
    except BusinessDenied:
        return {"ok": False, "error": "permission_denied"}
    except Exception as exc:  # health: allow BLE001 -- control handlers return structured errors
        logger.warning("Error in business control request: %s", exc, exc_info=True)
        return {"ok": False, "error": "internal_error"}


def register_business_control_handlers(server: Any, service: BusinessService) -> None:
    """Wire business control verbs onto GatewayControlServer private_handlers."""
    def handle_request(params: dict[str, Any], peer_subject: str) -> dict[str, Any]:
        principal = Principal(
            subject=peer_subject,
            runtime_profile="default",
            operations=frozenset({"request", "submit", "approve"}),
            kind="peer",
        )
        op = params.get("operation", "")
        p = params.get("params", {})
        return handle_business_request(service, operation=op, params=p, principal=principal)

    def handle_transport(params: dict[str, Any], peer_subject: str) -> dict[str, Any]:
        principal = Principal(
            subject=peer_subject,
            runtime_profile="default",
            operations=frozenset({"transport"}),
            kind="transport",
        )
        return handle_business_request(service, operation="business.consume", params=params, principal=principal)

    server.private_handlers["business.request"] = handle_request
    server.private_handlers["business.transport"] = handle_transport
