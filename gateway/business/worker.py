"""Profile-bound native dispatch lifecycle and persistent bridge supervision latch."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from gateway.business.models import AccountKey, Scope
from gateway.business.readiness import get_account_mode

logger = logging.getLogger(__name__)


def delivery_adapter(runner: Any, account: AccountKey):
    """Resolve the physical owner; a secondary never borrows the default bot."""
    if runner is None:
        return None
    from gateway.config import Platform
    by_profile = getattr(runner, "_adapters_for_profile", None)
    if callable(by_profile):
        adapters = by_profile(account.transport_profile)
        return adapters.get(Platform.WHATSAPP) if isinstance(adapters, dict) else None
    # Gateway exposes its ownership table through _adapters_by_profile.
    table = getattr(runner, "_profile_adapters", {})
    owned = table.get(account.transport_profile, {})
    if isinstance(owned, dict) and owned:
        return owned.get(Platform.WHATSAPP) or owned.get("whatsapp")
    adapters = getattr(runner, "adapters", {})
    adapter = adapters.get(Platform.WHATSAPP) or adapters.get("whatsapp")
    owner = getattr(adapter, "_hermes_profile_name", "default") if adapter else None
    return adapter if owner == account.transport_profile else None


def latch_bridge_account(adapter: Any, account: AccountKey, home: Path) -> None:
    """Latch before activation; every endpoint rereads this fsynced file on every call."""
    session = Path(adapter._session_path)
    session.mkdir(parents=True, exist_ok=True)
    target = session / "business-latch.json"
    expected = {"transport_profile": account.transport_profile, "account_id": account.account_id,
                "home": str(Path(home).absolute())}
    if target.exists():
        current = json.loads(target.read_text(encoding="utf-8-sig"))
        if any(current.get(k) != expected[k] for k in ("transport_profile", "account_id")):
            raise ValueError("foreign_business_latch")
        if current == expected:
            return
    raw = json.dumps(expected, sort_keys=True).encode()
    temporary = target.with_suffix(".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    adapter._business_account = account


async def dispatch_tick(runner: Any, service: Any) -> None:
    """Process queued jobs and at most one physical part for each latched account."""
    from gateway.business.jobs import process_jobs
    from gateway.business.readiness import check_readiness
    from gateway.business.transport import dispatch_due
    await process_jobs(service)
    with contextlib.closing(service.store._connect()) as db:
        rows = db.execute("SELECT DISTINCT runtime_profile,transport_profile,account_id FROM business_account_modes WHERE mode='supervised'").fetchall()
    for row in rows:
        account = AccountKey(row["transport_profile"], row["account_id"])
        scope = Scope(row["runtime_profile"], account.transport_profile, account.account_id, "")
        cfg = service.resolve_config(scope)
        if not cfg or cfg.mode != "supervised" or not cfg.governor:
            continue
        adapter = delivery_adapter(runner, account)
        if adapter is None:
            continue
        adapter._business_store = service.store
        adapter._business_service = service
        latch_bridge_account(adapter, account, service.store.db_path.parent)
        recovered = getattr(service, "_recovered_transport_accounts", set())
        if account not in recovered:
            from gateway.business.recovery import reconcile_account
            journal_path = Path(adapter._session_path) / "business-attempts.jsonl"
            journal = []
            if journal_path.exists():
                for line in journal_path.read_text(encoding="utf-8-sig").splitlines():
                    try:
                        journal.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Ignored incomplete bridge journal record")
            await reconcile_account(service, account, journal=tuple(journal))
            recovered.add(account)
            service._recovered_transport_accounts = recovered
        with service.runtime_scope(scope) if service.runtime_scope else contextlib.nullcontext():
            readiness = await check_readiness(service, account, runtime_profile=row["runtime_profile"])
            if readiness.get("ready") and get_account_mode(service.store, account, row["runtime_profile"]) == "supervised":
                await dispatch_due(service, account)


async def _dispatch_loop(runner: Any, service: Any) -> None:
    while True:
        try:
            await dispatch_tick(runner, service)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Business dispatch tick held")
        await asyncio.sleep(1)


def start_dispatch_worker(runner: Any, service: Any) -> asyncio.Task | None:
    service._transport_runner = runner
    maps = [getattr(runner, "adapters", {})]
    maps.extend(getattr(runner, "_profile_adapters", {}).values())
    for adapters in maps:
        for adapter in adapters.values():
            if hasattr(adapter, "dispatch_business_part"):
                adapter._business_store = service.store
                adapter._business_service = service
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    existing = getattr(runner, "_business_dispatch_task", None)
    if existing and not existing.done():
        return existing
    task = loop.create_task(_dispatch_loop(runner, service), name="business-dispatch")
    runner._business_dispatch_task = task
    return task


async def stop_dispatch_worker(runner: Any) -> None:
    task = getattr(runner, "_business_dispatch_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    runner._business_dispatch_task = None


async def stop_business_runtime(runner: Any) -> None:
    from gateway.business.lifecycle import stop_business_service
    await stop_dispatch_worker(runner)
    stop_business_service(runner)
