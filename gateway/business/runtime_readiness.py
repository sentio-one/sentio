"""Probe actual runtime capabilities instead of treating configuration as evidence."""

from contextlib import nullcontext

from gateway.business.models import EpisodeRef, Scope


async def probe_runtime(service, account, runtime_profile):
    from gateway.business.worker import delivery_adapter
    from gateway.business.policy import collect_policy
    from hermes_cli.plugins_business import callbacks_for_required_policy

    blockers = []
    from gateway.business.sandbox_boundary import agent_tools_sandboxed
    if not agent_tools_sandboxed(service, runtime_profile):
        blockers.append("agent_tools_not_sandboxed")
    runner = getattr(service, "runner", None)
    server = getattr(runner, "session_control_server", None)
    handlers = getattr(server, "private_handlers", {})
    if not {"business.request", "business.transport"} <= handlers.keys():
        blockers.append("operator_controls_unavailable")
    adapter = delivery_adapter(runner, account)
    if adapter is None or not hasattr(adapter, "get_business_capabilities"):
        blockers.append("bridge_protocol_unavailable")
    else:
        try:
            capabilities = await adapter.get_business_capabilities()
            if any(capabilities.get(key) != 1 for key in ("businessDispatch", "businessReceipts", "businessLatch")):
                blockers.append("bridge_protocol_unavailable")
        except (OSError, ValueError, TimeoutError):
            blockers.append("bridge_protocol_unavailable")
    scope = Scope(runtime_profile, account.transport_profile, account.account_id, "")
    cfg = service.resolve_config(scope)
    if cfg and cfg.required_policies:
        with service.runtime_scope(scope) if service.runtime_scope else nullcontext():
            bundle = await collect_policy(scope, EpisodeRef("readiness", 0), required_policies=cfg.required_policies)
            if bundle.missing or any(len(callbacks_for_required_policy(owner, "business_check")) != 1 for owner in cfg.required_policies):
                blockers.append("required_policy_unavailable")
            if len(bundle.capabilities) != len(cfg.required_policies) or any(
                cap.get("business_jobs") != 1 or cap.get("mode") != "native" for cap in bundle.capabilities
            ):
                blockers.append("native_plugin_mode_unverified")
    try:
        with service.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("SELECT 1 FROM business_attempts LIMIT 1")
            db.rollback()
    except OSError:
        blockers.append("store_unavailable")
    return blockers
