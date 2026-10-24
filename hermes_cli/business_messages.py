"""Formatters and handlers for ``hermes messages`` CLI command (Task C1)."""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from hermes_cli.sqlite_util import transaction

if TYPE_CHECKING:
    from gateway.business.models import Scope
    from gateway.business.store import BusinessStore


def format_message_item(store: BusinessStore, scope: Scope, outbox_id: str) -> dict[str, Any]:
    """Retrieve and format an outbox message item distinctly representing held vs delivery states."""
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute(
            "SELECT outbox_id, runtime_profile, transport_profile, account_id, contact_id, "
            "       revision_id, plan_id, request_id, state, created_at_ms, expires_at_ms, reason "
            "FROM business_outbox WHERE outbox_id=?",
            (outbox_id,),
        ).fetchone()

        if not row:
            return {"id": outbox_id, "state": "unknown", "error": "not_found"}

        # Check attempts to see if an attempt has advanced (e.g. accepted, sent, delivered)
        attempts = db.execute(
            "SELECT state, platform_ids FROM business_attempts WHERE outbox_id=? ORDER BY part_index ASC",
            (outbox_id,),
        ).fetchall()

        effective_state = row["state"]
        if attempts:
            attempt_states = {a["state"] for a in attempts}
            if "delivered" in attempt_states and len(attempt_states) == 1:
                effective_state = "delivered"
            elif "accepted" in attempt_states:
                effective_state = "accepted"
            elif "sent" in attempt_states:
                effective_state = "sent"
            elif "unknown" in attempt_states:
                effective_state = "unknown"

        return {
            "id": row["outbox_id"],
            "state": effective_state,
            "revision_id": row["revision_id"],
            "plan_id": row["plan_id"],
            "request_id": row["request_id"],
            "scope": {
                "runtime_profile": row["runtime_profile"],
                "transport_profile": row["transport_profile"],
                "account_id": row["account_id"],
                "contact_id": row["contact_id"],
            },
            "created_at_ms": row["created_at_ms"],
            "expires_at_ms": row["expires_at_ms"],
            "reason": row["reason"],
        }


def run_messages_command(args: Any) -> int:
    """CLI executor for ``hermes messages``."""
    return 0
