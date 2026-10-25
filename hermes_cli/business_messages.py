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
            "FROM business_outbox WHERE outbox_id=? AND runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
            (outbox_id,scope.runtime_profile,scope.transport_profile,scope.account_id,scope.contact_id),
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
    from gateway.business.client import request_business
    from gateway.business.models import BusinessDenied, BusinessProtocolError
    from hermes_constants import get_hermes_home
    from pathlib import Path

    command = getattr(args, "messages_command", None)
    if not command:
        print(json.dumps({"ok": False, "error": "subcommand_required"}))
        return 2
    params = {key: value for key, value in vars(args).items()
              if key in {"outbox_id", "revision_id", "plan_digest", "override_reason", "reason",
                         "text", "task_id", "mode", "runtime_profile", "transport_profile", "account_id",
                         "contact_id", "lesson_id", "policy_id", "texts", "evidence_revision_ids",
                         "vault_item_id", "expires_at_ms", "path", "manifest_id", "source_digest", "legacy_stopped"}
              and value is not None}
    if getattr(args, "profile", None) and "runtime_profile" not in params:
        params["runtime_profile"] = args.profile
    home = Path(getattr(args, "home", None) or get_hermes_home())
    action = "set_mode" if command == "mode" else command.replace("-", "_")
    try:
        result = request_business(home, operation=f"operator.{action}", params=params, timeout=120)
    except (BusinessDenied, BusinessProtocolError) as exc:
        result = {"ok": False, "error": str(exc) or "permission_denied"}
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("ok") is True else 1
