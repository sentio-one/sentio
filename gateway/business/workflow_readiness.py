"""Workflow readiness checks, protocol evaluation, and pilot handoff reporting (Task C7)."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from gateway.business.models import AccountKey, Scope

if TYPE_CHECKING:
    from gateway.business.migration import CutoverManifest
    from gateway.business.service import BusinessService
    from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)


async def check_workflow_readiness(
    service: BusinessService,
    account: AccountKey,
    *,
    credentials_ready: bool | None = None,
    cutover_manifest: CutoverManifest | None = None,
    legacy_writers: tuple[dict[str, Any], ...] = (),
    runtime_profile: str = "default",
) -> dict[str, Any]:
    """Verify end-to-end supervised workflow prerequisites across controls, policy, cutover, and credentials."""
    blockers: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}

    scope = Scope(runtime_profile, account.transport_profile, account.account_id, "")
    cfg = None
    try:
        cfg = service.resolve_config(scope)
    except (KeyError, ValueError, TypeError) as exc:
        logger.debug("Failed resolving config during workflow readiness check: %s", exc, exc_info=True)

    # 1. Operators check
    has_operators = bool(cfg and cfg.operators)
    checks["operators"] = has_operators
    if not has_operators:
        blockers.append("missing_operators")

    # 2. Required policy check
    has_policy = bool(cfg and cfg.required_policies)
    checks["required_policy"] = has_policy
    if not has_policy:
        blockers.append("missing_policy")

    extra = getattr(cfg, "extra", {}) or {}

    # 3. Native plugin mode check
    plugin_mode = extra.get("plugin_mode", "unverified")
    is_native = plugin_mode == "native"
    checks["native_plugin_mode"] = is_native
    if not is_native:
        blockers.append(f"plugin_mode_not_native:{plugin_mode}")

    # 4. Credential capability check
    creds_required = extra.get("credentials_required", False)
    if credentials_ready is False:
        checks["credentials"] = False
        blockers.append("missing_credentials_capability")
    elif creds_required and credentials_ready is not True:
        try:
            from agent.vault_store import get_vault_store

            get_vault_store()
            checks["credentials"] = True
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("Vault store unresolvable for credential check: %s", exc)
            checks["credentials"] = False
            blockers.append("missing_credentials_capability")
    else:
        checks["credentials"] = True

    # 5. Import and cutover manifest check
    if cutover_manifest is not None:
        is_ready_manifest = cutover_manifest.stage in ("ready", "cutover_applied") and not cutover_manifest.blockers
        checks["migration_manifest"] = is_ready_manifest
        if cutover_manifest.stage not in ("ready", "cutover_applied"):
            blockers.append(f"incomplete_migration:{cutover_manifest.stage}")
        if cutover_manifest.blockers:
            blockers.extend(cutover_manifest.blockers)
    else:
        migration_required = extra.get("migration_required", False)
        if migration_required:
            blockers.append("incomplete_migration:missing_manifest")
            checks["migration_manifest"] = False
        else:
            checks["migration_manifest"] = True

    # 6. Exclusive ownership check (competing legacy writers)
    writers = legacy_writers or extra.get("legacy_writers", ())
    active_writers = [w.get("name", "unknown") for w in writers if w.get("status") != "stopped"]
    has_exclusive_ownership = len(active_writers) == 0
    checks["exclusive_ownership"] = has_exclusive_ownership
    if active_writers:
        blockers.append(f"competing_legacy_writers:{','.join(active_writers)}")

    from gateway.business.runtime_readiness import probe_runtime
    blockers.extend(await probe_runtime(service, account, runtime_profile))
    # Native mode is established by the callback capability, never by a missing flag.
    if "native_plugin_mode_unverified" not in blockers and "required_policy_unavailable" not in blockers:
        checks["native_plugin_mode"] = True
        if plugin_mode == "unverified":
            blockers = [b for b in blockers if b != "plugin_mode_not_native:unverified"]
    ready = len(blockers) == 0
    return {
        "ready": ready,
        "workflow_protocol": 1 if ready else None,
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
    }


def pilot_report(
    scope: Scope,
    *,
    store: BusinessStore | None = None,
) -> dict[str, Any]:
    """Report supervised test evidence without promoting business mode or emitting live sends."""
    from gateway.business.readiness import get_account_mode

    account = AccountKey(transport_profile=scope.transport_profile, account_id=scope.account_id)
    current_mode = get_account_mode(store, account, runtime_profile=scope.runtime_profile) if store else "off"

    episodes_count = 0
    lessons_count = 0
    reviews_count = 0

    if store is not None:
        try:
            conn = store._connect()
            with conn:
                r_ep = conn.execute(
                    "SELECT COUNT(*) AS c FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=?",
                    (scope.runtime_profile, scope.transport_profile, scope.account_id),
                ).fetchone()
                episodes_count = int(r_ep["c"]) if r_ep else 0

                r_lsn = conn.execute(
                    "SELECT COUNT(*) AS c FROM business_lessons WHERE runtime_profile=? AND transport_profile=? AND account_id=?",
                    (scope.runtime_profile, scope.transport_profile, scope.account_id),
                ).fetchone()
                lessons_count = int(r_lsn["c"]) if r_lsn else 0

                r_rev = conn.execute(
                    "SELECT COUNT(*) AS c FROM business_reviews WHERE runtime_profile=? AND transport_profile=? AND account_id=?",
                    (scope.runtime_profile, scope.transport_profile, scope.account_id),
                ).fetchone()
                reviews_count = int(r_rev["c"]) if r_rev else 0
        except (OSError, KeyError, ValueError) as exc:
            logger.warning("Failed querying evidence metrics in pilot_report: %s", exc)

    return {
        "ready": False,
        "auto_graduation_refused": True,
        "human_approval_required": True,
        "sends_emitted": 0,
        "mode_promoted": False,
        "current_mode": current_mode,
        "evidence": {
            "episodes": episodes_count,
            "reviews": reviews_count,
            "lessons": lessons_count,
            **_measured_evidence(store, scope),
        },
        "authorizations": {
            "pilot_handoff_certified": False,
            "live_resume_authorized": False,
        },
        "unknowns": [
            "live_baileys_session_pairing",
            "physical_recipient_pacing_drift",
            "operator_realtime_alert_channel",
        ],
    }


def _measured_evidence(store, scope):
    """No observations means unknown; mixed policy/model cohorts remain separate."""
    from gateway.business.measurements import measured_evidence
    return measured_evidence(store, scope)
