"""Gateway lifecycle for held business accounts, controls, and the outbox worker."""

import asyncio
from pathlib import Path

from gateway.business.models import AccountKey, Scope
from gateway.business.runtime_config import RuntimeConfig, raw_business
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from hermes_constants import get_hermes_home, profile_name_for_home


def _has_latched_account(store):
    with store._connect() as conn:
        return conn.execute("SELECT 1 FROM business_account_modes WHERE mode='supervised' LIMIT 1").fetchone() is not None


async def start_business_service(runner, *, start_worker=True):
    home = Path(getattr(runner, "hermes_home", None) or get_hermes_home())
    configs = [runner.config, *(getattr(runner, "_profile_configs", {}) or {}).values()]
    configured = any(_requested(raw_business(cfg)) for cfg in configs)
    if not configured and not (home / "state.db").exists():
        runner._business_store = runner._business_service = None
        return None
    store = getattr(runner, "_business_store", None) or BusinessStore(home)
    if not configured and not _has_latched_account(store):
        runner._business_store = runner._business_service = None
        return None
    resolver = RuntimeConfig(runner, store)
    service = BusinessService(store, config_resolver=resolver, runtime_scope=resolver.runtime_scope)
    service.runner = runner
    service.runtime_profile = profile_name_for_home(home) or "default"
    service.account_scope = Scope(service.runtime_profile, service.runtime_profile, service.runtime_profile, "")
    service.main_runtime = getattr(runner, "_main_runtime", {})
    runner._business_store, runner._business_service = store, service
    server = getattr(runner, "session_control_server", None)
    if server is not None:
        from gateway.business.control import register_business_control_handlers
        register_business_control_handlers(server, service, runner=runner, loop=asyncio.get_running_loop())
    from gateway.business.worker import latch_bridge_account, start_dispatch_worker
    for profile in resolver.profiles():
        cfg = resolver(profile)
        if cfg and cfg.mode == "supervised":
            adapter = _whatsapp_adapter(runner, profile)
            if adapter is not None:
                latch_bridge_account(adapter, AccountKey(profile, profile), home)
    if start_worker:
        start_dispatch_worker(runner, service)
    return service


def _requested(raw):
    return bool(raw) and (not isinstance(raw, dict) or raw.get("mode", "off") != "off")


def _whatsapp_adapter(runner, profile):
    from gateway.config import Platform
    if hasattr(runner, "_adapters_for_profile"):
        return runner._adapters_for_profile(profile).get(Platform.WHATSAPP)
    return (getattr(runner, "adapters", {}) or {}).get(Platform.WHATSAPP)


def stop_business_service(runner):
    """Compatibility for synchronous harness teardown; real shutdown drains the worker first."""
    task = getattr(runner, "_business_dispatch_task", None)
    if task is not None:
        task.cancel()
    runner._business_service = runner._business_store = None
