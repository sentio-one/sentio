"""Required, bounded policy contributions from profile-scoped plugins."""

from __future__ import annotations

import asyncio
import inspect
import threading
from dataclasses import dataclass
from typing import Any

from hermes_cli.config import load_config_readonly
from hermes_cli.plugins import discover_plugins
from hermes_cli.plugins_business import callbacks_for_required_policy
from gateway.business.models import ActionReceipt, Candidate, CheckReport, EpisodeRef, Fact, Scope

POLICY_TIMEOUT_SECONDS = 10
_worker_lock = threading.Lock()
_running_workers = 0
_MAX_RUNNING_WORKERS = 32


@dataclass(frozen=True)
class PolicyBundle:
    id: str
    version: str
    facts: tuple[Fact, ...]
    actions: tuple[ActionReceipt, ...]
    rules: tuple[str, ...]
    criteria: tuple[str, ...]
    missing: tuple[str, ...]


def _requirements() -> tuple[str, ...]:
    cfg = load_config_readonly()
    business = (((cfg.get("platforms") or {}).get("whatsapp") or {}).get("extra") or {}).get("business") or {}
    policies = business.get("required_policies", ())
    if not isinstance(policies, (list, tuple)) or any(not isinstance(item, str) or not item for item in policies):
        return ("required_policy_invalid",)
    if business.get("mode", "off") != "off" and not policies:
        return ("required_policy_unconfigured",)
    return tuple(policies)


async def _invoke(callback: Any, **kwargs: Any) -> Any:
    global _running_workers
    loop = asyncio.get_running_loop()
    result = loop.create_future()
    with _worker_lock:
        if _running_workers >= _MAX_RUNNING_WORKERS:
            raise TimeoutError("required business policy worker limit reached")
        _running_workers += 1

    def finish(value=None, error=None):
        def set_result():
            if result.done():
                return
            if error is not None:
                result.set_exception(error)
            else:
                result.set_result(value)
        loop.call_soon_threadsafe(set_result)

    def run():
        global _running_workers
        try:
            value = callback(**kwargs)
            if inspect.isawaitable(value):
                value = asyncio.run(value)
            finish(value=value)
        except BaseException as exc:  # health: allow BLE001 -- remote/plugin failures are blocking policy errors
            finish(error=exc)
        finally:
            with _worker_lock:
                _running_workers -= 1

    threading.Thread(target=run, name="business-policy", daemon=True).start()
    return await asyncio.wait_for(result, timeout=POLICY_TIMEOUT_SECONDS)


def _sequence(value: object, key: str, expected: type) -> tuple:
    rows = value.get(key, []) if isinstance(value, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid policy result")
    return tuple(expected(**row) for row in rows)


async def collect_policy(scope: Scope, episode: EpisodeRef) -> PolicyBundle:
    """Load each configured owner from discovery and aggregate only validated JSON results."""
    discover_plugins()
    requirements = _requirements()
    facts: list[Fact] = []
    actions: list[ActionReceipt] = []
    rules: list[str] = []
    criteria: list[str] = []
    missing: list[str] = []
    versions: list[str] = []
    for plugin_id in requirements:
        callbacks = callbacks_for_required_policy(plugin_id, "business_context")
        if len(callbacks) != 1:
            missing.append(plugin_id)
            continue
        try:
            raw = await _invoke(callbacks[0], episode={"id": episode.id, "context_version": episode.context_version},
                                scope={"runtime_profile": scope.runtime_profile,
                                       "transport_profile": scope.transport_profile,
                                       "account_id": scope.account_id, "contact_id": scope.contact_id})
            if not isinstance(raw, dict) or not isinstance(raw.get("version"), str):
                raise ValueError("invalid policy result")
            callback_missing = raw.get("missing", [])
            if not isinstance(callback_missing, list) or any(not isinstance(item, str) for item in callback_missing):
                raise ValueError("invalid policy result")
            missing.extend(f"{plugin_id}:{item}" for item in callback_missing)
            facts.extend(_sequence(raw, "facts", Fact))
            actions.extend(_sequence(raw, "actions", ActionReceipt))
            for field, target in (("rules", rules), ("criteria", criteria)):
                entries = raw.get(field, [])
                if not isinstance(entries, list) or any(not isinstance(v, str) or not v.strip() for v in entries):
                    raise ValueError("invalid policy result")
                target.extend(entries)
            versions.append(raw["version"])
        except Exception:  # health: allow BLE001 -- plugin policy failure marks policy missing
            missing.append(plugin_id)
    return PolicyBundle(id=",".join(requirements), version="+".join(versions) or "unavailable",
                        facts=tuple(facts), actions=tuple(actions), rules=tuple(rules),
                        criteria=tuple(criteria), missing=tuple(missing))


async def check_candidate(scope: Scope, candidate: Candidate, *, bundle: PolicyBundle) -> CheckReport:
    if bundle.missing:
        return CheckReport(False, tuple({"code": "policy_unavailable", "policy": p} for p in bundle.missing),
                           bundle.version)
    discover_plugins()
    issues: list[dict[str, Any]] = []
    for plugin_id in _requirements():
        callbacks = callbacks_for_required_policy(plugin_id, "business_check")
        if len(callbacks) != 1:
            issues.append({"code": "policy_unavailable", "policy": plugin_id})
            continue
        try:
            raw = await _invoke(callbacks[0], candidate={"kind": candidate.kind, "text": candidate.text,
                                "data": candidate.data, "fact_ids": candidate.fact_ids,
                                "completed_action_ids": candidate.completed_action_ids,
                                "credential_slots": candidate.credential_slots},
                                scope={"runtime_profile": scope.runtime_profile,
                                       "transport_profile": scope.transport_profile,
                                       "account_id": scope.account_id, "contact_id": scope.contact_id},
                                policy_version=bundle.version)
            if not isinstance(raw, dict) or not isinstance(raw.get("issues"), list):
                raise ValueError("invalid policy result")
            issues.extend(raw["issues"])
        except Exception:  # health: allow BLE001 -- plugin check failure marks policy unavailable
            issues.append({"code": "policy_unavailable", "policy": plugin_id})
    return CheckReport(not issues, tuple(issues), bundle.version)
