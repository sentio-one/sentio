"""Persistent supervision readiness, account latch, and bridge state invariants (Task B7)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from hermes_cli.sqlite_util import transaction
from gateway.business.models import AccountKey, Principal, Scope

if TYPE_CHECKING:
    from gateway.business.service import BusinessService
    from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)

VALID_MODES = frozenset({"off", "shadow", "supervised"})


@dataclass(frozen=True)
class BridgeState:
    state: str
    code: str | None
    respawn_count: int


async def check_readiness(service: BusinessService, account: AccountKey) -> dict[str, Any]:
    """Evaluate account readiness across config, store, bridge, and workflow latch."""
    blocking: list[str] = []
    warnings: list[str] = []

    scope = Scope("default", account.transport_profile, account.account_id, "")
    cfg = None
    try:
        cfg = service.resolve_config(scope)
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("Failed resolving config during readiness check: %s", exc, exc_info=True)

    if not cfg:
        blocking.append("missing_config")
    else:
        if not cfg.required_policies:
            blocking.append("missing_policy")
        if not cfg.operators:
            blocking.append("missing_operators")
        if not cfg.governor:
            blocking.append("missing_governor")

    # Bridge state check
    b_state = get_bridge_state(account, store=service.store)
    if b_state.state == "needs_reauth":
        blocking.append("bridge_needs_reauth")

    # Supervised activation strictly requires C7 workflow readiness capability
    from gateway.business.workflow_readiness import check_workflow_readiness

    wf = await check_workflow_readiness(service, account)
    if not wf.get("ready"):
        for b in wf.get("blockers", []):
            if b not in blocking:
                blocking.append(b)
        if not wf.get("blockers"):
            blocking.append("workflow_readiness_incomplete")

    return {
        "ready": len(blocking) == 0,
        "workflow_protocol": wf.get("workflow_protocol", 1) if len(blocking) == 0 else None,
        "blocking": blocking,
        "warnings": warnings,
    }


def get_account_mode(store: BusinessStore, account: AccountKey, runtime_profile: str = "default") -> str:
    """Retrieve the persisted business mode for an account ('off', 'shadow', 'supervised')."""
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute(
            "SELECT mode FROM business_account_modes "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=?",
            (runtime_profile, account.transport_profile, account.account_id),
        ).fetchone()
        if row:
            return str(row["mode"])
    return "off"


def _set_account_mode_row(store: BusinessStore, account: AccountKey, mode: str, runtime_profile: str = "default") -> None:
    now_ms = int(time.time() * 1000)
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        db.execute(
            "INSERT INTO business_account_modes(runtime_profile, transport_profile, account_id, mode, updated_at_ms) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(runtime_profile, transport_profile, account_id) DO UPDATE SET "
            "mode=excluded.mode, updated_at_ms=excluded.updated_at_ms",
            (runtime_profile, account.transport_profile, account.account_id, mode, now_ms),
        )


async def set_business_mode(
    principal: Principal,
    account: AccountKey,
    *,
    mode: str,
    service: BusinessService | None = None,
    store: BusinessStore | None = None,
    runtime_profile: str = "default",
) -> dict[str, Any]:
    """Persistently update the account business mode under authorization and readiness guards."""
    if mode not in VALID_MODES:
        return {"success": False, "mode": "invalid", "blocking": [f"unknown_mode:{mode}"]}

    active_store = store or (service.store if service else None)
    if not active_store:
        return {"success": False, "mode": "unknown", "blocking": ["store_unavailable"]}

    current_mode = get_account_mode(active_store, account, runtime_profile=runtime_profile)

    if mode == "supervised":
        if not service:
            return {"success": False, "mode": current_mode, "blocking": ["service_unavailable"]}
        readiness = await check_readiness(service, account)
        if not readiness["ready"]:
            return {
                "success": False,
                "mode": current_mode,
                "blocking": readiness["blocking"],
            }

    _set_account_mode_row(active_store, account, mode, runtime_profile=runtime_profile)
    return {"success": True, "mode": mode}


def record_bridge_state(
    account: AccountKey,
    *,
    state: str,
    code: str | None = None,
    store: BusinessStore | None = None,
) -> None:
    """Record bridge connection state, freezing auto-respawn loops on terminal reauth events."""
    if store is None:
        return

    now_ms = int(time.time() * 1000)
    respawn_count = 0
    normalized_state = state

    # 401 or logged_out / device_removed marks terminal needs_reauth
    code_str = str(code or "").lower()
    if "401" in code_str or "logged_out" in code_str or "device_removed" in code_str or state == "needs_reauth":
        normalized_state = "needs_reauth"
        respawn_count = 0

    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        db.execute(
            "INSERT INTO business_bridge_states(transport_profile, account_id, state, code, respawn_count, updated_at_ms) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(transport_profile, account_id) DO UPDATE SET "
            "state=excluded.state, code=excluded.code, respawn_count=excluded.respawn_count, updated_at_ms=excluded.updated_at_ms",
            (account.transport_profile, account.account_id, normalized_state, code, respawn_count, now_ms),
        )


def get_bridge_state(account: AccountKey, store: BusinessStore | None = None) -> BridgeState:
    """Retrieve the current recorded bridge status and respawn count for an account."""
    if store is None:
        return BridgeState(state="unknown", code=None, respawn_count=0)

    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute(
            "SELECT state, code, respawn_count FROM business_bridge_states "
            "WHERE transport_profile=? AND account_id=?",
            (account.transport_profile, account.account_id),
        ).fetchone()
        if row:
            return BridgeState(
                state=row["state"],
                code=row["code"],
                respawn_count=row["respawn_count"],
            )

    return BridgeState(state="disconnected", code=None, respawn_count=0)
