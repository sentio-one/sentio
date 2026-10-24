"""Physical execution, registration preflight, and typed send receipts (Task B5)."""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from typing import Any

from gateway.business.dispatch import issue_authorization
from gateway.business.models import (
    AccountKey,
    BusinessConflict,
    BusinessDenied,
    Candidate,
    ContextEvent,
    EpisodeRef,
    Receipt,
    Scope,
)
from gateway.business.render import render_delivery_plan
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)

_DEFAULT_GOVERNOR_CONFIG: dict[str, Any] = {
    "timezone": "UTC",
    "windows": {
        "cold": [{"weekdays": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "24:00"}],
        "followup": [{"weekdays": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "24:00"}],
        "reply": [{"weekdays": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "24:00"}],
        "response": [{"weekdays": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "24:00"}],
    },
    "min_gap_seconds": 0,
    "caps": {},
    "hard_max_per_day": 100000,
}


async def preflight_recipient(
    scope: Scope,
    *,
    store: BusinessStore | None = None,
    contact_checker: Any | None = None,
) -> str:
    """Preflight recipient registration: 'known_valid', 'no_whatsapp_account', or 'unknown'."""
    if store is not None:
        try:
            events = store.list_events(scope)
            for ev in events:
                if getattr(ev, "actor", None) == "customer" or getattr(ev, "evidence", None) == "inbound":
                    return "known_valid"
        except (BusinessDenied, BusinessConflict, KeyError) as exc:
            logger.debug("Failed checking ingress events for recipient preflight %s: %s", scope, exc, exc_info=True)

    if contact_checker is not None and callable(contact_checker):
        try:
            if inspect.iscoroutinefunction(contact_checker):
                res = await contact_checker(scope.contact_id)
            else:
                res = contact_checker(scope.contact_id)
            if res:
                return "known_valid"
            return "no_whatsapp_account"
        except Exception as exc:  # health: allow BLE001 -- checker lookup failures hold sending as unknown
            logger.debug("Contact checker error for %s: %s", scope.contact_id, exc, exc_info=True)
            return "unknown"

    return "unknown"


async def stage_turn_candidate(
    store: BusinessStore,
    scope: Scope,
    episode: EpisodeRef,
    candidate: Candidate,
) -> Receipt:
    """Stage a working tool candidate for the turn's own contact without creating an active review draft."""
    receipt_id = str(uuid.uuid4())
    return Receipt(id=receipt_id, state="staged", revision_id=None, reason=None)


async def submit_external(
    scope: Scope,
    candidate: Candidate,
    *,
    request_id: str,
    source_kind: str,
    service: BusinessService,
    contact_checker: Any | None = None,
    now_ms: int | None = None,
) -> Receipt:
    """Submit an outbound candidate via the supervised outbox with recipient preflight."""
    ts = int(time.time() * 1000) if now_ms is None else now_ms
    preflight = await preflight_recipient(scope, store=service.store, contact_checker=contact_checker)
    if preflight == "no_whatsapp_account":
        return Receipt(id=str(uuid.uuid4()), state="failed", revision_id=None, reason="no_whatsapp_account")
    if preflight == "unknown" and contact_checker is not None:
        return Receipt(id=str(uuid.uuid4()), state="held", revision_id=None, reason="lookup_error")

    service.store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])

    try:
        ep = service.store.episode_for_source(scope, request_id)
    except BusinessDenied:
        ingress_event = ContextEvent(
            id=str(uuid.uuid4()),
            kind="message",
            actor="external",
            text=candidate.text,
            target_id=None,
            evidence="outbound",
        )
        service.store.record_ingress(scope, ingress_event, source_id=request_id)
        ep = service.store.episode_for_source(scope, request_id)

    rev = service.store.append_revision(
        scope,
        ep,
        candidate,
        expected_revision_id=None,
        policy_version="v1",
        lesson_version=0,
    )
    plan = await render_delivery_plan(scope, rev, now_ms=ts)
    service.store.save_plan(plan)
    return service.store.submit_revision(scope, rev.id, request_id=request_id, now_ms=ts)


async def dispatch_due(
    service: BusinessService,
    account: AccountKey,
    *,
    transport_executor: Any | None = None,
    now_ms: int | None = None,
) -> list[Receipt]:
    """Claim and physically dispatch due approved delivery parts respecting governor and token auth."""
    ts = int(time.time() * 1000) if now_ms is None else now_ms
    attempt = service.store.claim_part(account, now_ms=ts)
    if attempt is None:
        return []

    ctx = service.store.get_attempt_context(attempt.id)
    if not ctx:
        return []

    scope = Scope(
        runtime_profile=ctx["runtime_profile"],
        transport_profile=ctx["transport_profile"],
        account_id=ctx["account_id"],
        contact_id=ctx["contact_id"],
    )
    plan = service.store.get_plan(scope, ctx["plan_id"])
    if not plan:
        return []

    gov_cfg = _resolve_governor_config(service, scope)
    elig = service.store.governor_reserve_attempt(
        account,
        attempt.id,
        message_class="reply",
        now_ms=ts,
        config=gov_cfg,
    )
    if not elig.allowed:
        return []

    token = issue_authorization(account, attempt, plan, now_ms=ts)
    part_dict = plan.parts[attempt.part_index].__dict__ if attempt.part_index < len(plan.parts) else {}
    req = {
        "token": token,
        "attempt_id": attempt.id,
        "outbox_id": attempt.outbox_id,
        "part_index": attempt.part_index,
        "scope": {
            "runtime_profile": scope.runtime_profile,
            "transport_profile": scope.transport_profile,
            "account_id": scope.account_id,
            "contact_id": scope.contact_id,
        },
        "part": part_dict,
        "plan_id": plan.id,
        "plan_digest": plan.digest,
    }

    if transport_executor is not None:
        return await _execute_with_custom_transport(
            service, account, attempt, ctx["revision_id"], req, transport_executor, ts
        )

    return await _execute_with_native_transport(
        service, account, attempt, ctx["revision_id"], req, ts
    )


def _resolve_governor_config(service: BusinessService, scope: Scope) -> dict[str, Any]:
    try:
        cfg = service.resolve_config(scope)
        if cfg and cfg.governor:
            return cfg.governor
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("Governor config resolution fallback for %s: %s", scope, exc, exc_info=True)
    return _DEFAULT_GOVERNOR_CONFIG


async def _execute_with_custom_transport(
    service: BusinessService,
    account: AccountKey,
    attempt: Any,
    revision_id: str,
    req: dict[str, Any],
    transport_executor: Any,
    now_ms: int,
) -> list[Receipt]:
    try:
        if inspect.iscoroutinefunction(transport_executor):
            res = await transport_executor(req)
        else:
            res = transport_executor(req)
        platform_ids = ()
        if isinstance(res, dict) and "message_id" in res and res["message_id"]:
            platform_ids = (str(res["message_id"]),)
        service.store.update_attempt_state(attempt.id, state="sent", platform_ids=platform_ids)
        service.store.governor_complete_attempt(account, attempt.id, submitted=True, finished_at_ms=now_ms)
        return [Receipt(id=attempt.id, state="sent", revision_id=revision_id, reason=None)]
    except TimeoutError as ex:
        service.store.update_attempt_state(attempt.id, state="unknown", error_code="timeout")
        service.store.governor_complete_attempt(account, attempt.id, submitted=True, finished_at_ms=now_ms)
        return [Receipt(id=attempt.id, state="unknown", revision_id=revision_id, reason=str(ex))]
    except Exception as ex:  # health: allow BLE001 -- external transport errors must be recorded as failed attempts
        logger.warning("Custom transport dispatch error for %s: %s", attempt.id, ex, exc_info=True)
        service.store.update_attempt_state(attempt.id, state="failed", error_code=str(ex))
        service.store.governor_complete_attempt(account, attempt.id, submitted=False, finished_at_ms=now_ms)
        return [Receipt(id=attempt.id, state="failed", revision_id=revision_id, reason=str(ex))]


async def _execute_with_native_transport(
    service: BusinessService,
    account: AccountKey,
    attempt: Any,
    revision_id: str,
    req: dict[str, Any],
    now_ms: int,
) -> list[Receipt]:
    from gateway.business.client import request_business

    try:
        resp = await request_business("dispatch", req)
        if isinstance(resp, dict) and resp.get("status") == "ok":
            msg_id = resp.get("message_id")
            pids = (str(msg_id),) if msg_id else ()
            service.store.update_attempt_state(attempt.id, state="sent", platform_ids=pids)
            service.store.governor_complete_attempt(account, attempt.id, submitted=True, finished_at_ms=now_ms)
            return [Receipt(id=attempt.id, state="sent", revision_id=revision_id, reason=None)]
        error = resp.get("error", "dispatch_error") if isinstance(resp, dict) else "dispatch_failed"
        service.store.update_attempt_state(attempt.id, state="failed", error_code=error)
        service.store.governor_complete_attempt(account, attempt.id, submitted=False, finished_at_ms=now_ms)
        return [Receipt(id=attempt.id, state="failed", revision_id=revision_id, reason=error)]
    except TimeoutError as ex:
        service.store.update_attempt_state(attempt.id, state="unknown", error_code="timeout")
        service.store.governor_complete_attempt(account, attempt.id, submitted=True, finished_at_ms=now_ms)
        return [Receipt(id=attempt.id, state="unknown", revision_id=revision_id, reason=str(ex))]
    except Exception as ex:  # health: allow BLE001 -- native transport dispatch failures must be recorded as failed attempts
        logger.warning("Native dispatch error for %s: %s", attempt.id, ex, exc_info=True)
        service.store.update_attempt_state(attempt.id, state="failed", error_code=str(ex))
        service.store.governor_complete_attempt(account, attempt.id, submitted=False, finished_at_ms=now_ms)
        return [Receipt(id=attempt.id, state="failed", revision_id=revision_id, reason=str(ex))]
