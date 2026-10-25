"""Physical execution, registration preflight, and typed send receipts (Task B5)."""

from __future__ import annotations

import inspect
from dataclasses import asdict
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
    main_runtime: dict[str, Any] | None = None,
) -> Receipt:
    """Submit an outbound candidate via the supervised outbox with recipient preflight."""
    ts = int(time.time() * 1000) if now_ms is None else now_ms
    preflight = await preflight_recipient(scope, store=service.store, contact_checker=contact_checker)
    if preflight == "no_whatsapp_account":
        return Receipt(id=str(uuid.uuid4()), state="failed", revision_id=None, reason="no_whatsapp_account")
    if preflight == "unknown" and contact_checker is not None:
        return Receipt(id=str(uuid.uuid4()), state="held", revision_id=None, reason="lookup_error")

    # Producer targets are aliases; every durable record uses the canonical contact.
    with service.store._connect() as db:
        canonical = db.execute("SELECT 1 FROM business_contacts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                               tuple(asdict(scope).values())).fetchone()
    if not canonical:
        scope = service.store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])

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

    from gateway.business.policy import collect_policy
    from gateway.business.context import build_context
    from gateway.business.review import prepare_draft
    from gateway.business.drafting import promote_draft
    from gateway.business.models import WorkDecision
    cfg = service.resolve_config(scope)
    if not cfg or cfg.mode != "supervised":
        return Receipt(id=str(uuid.uuid4()), state="held", revision_id=None, reason="business_unavailable")
    policy = await collect_policy(scope, ep, required_policies=cfg.required_policies)
    snapshot = build_context(service.store, scope, ep, policy=policy, lesson_version=0)
    decision = WorkDecision("compose", source_kind, "Review submitted outbound candidate", (), (), policy.version)
    receipt = await prepare_draft(service.store, snapshot, decision, policy=policy,
                                  main_runtime=main_runtime or {}, rewrite_attempts=cfg.rewrite_attempts,
                                  initial_candidate=candidate)
    return await promote_draft(service.store, scope, receipt, cfg, now_ms=ts, request_id=request_id)


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
        from gateway.business.store_outbox import reschedule_attempt
        reschedule_attempt(service.store, attempt.id, not_before_ms=elig.not_before_ms or ts + 1000,
                           reason=elig.reason or "governor_hold")
        return []

    from gateway.business.dispatch import _default_transport_key
    recipient = resolve_transport_target(service.store, scope)
    token = issue_authorization(account, attempt, plan, now_ms=ts, recipient=recipient,
                                secret_key=_default_transport_key(service.store.db_path.parent))
    part_dict = asdict(plan.parts[attempt.part_index]) if attempt.part_index < len(plan.parts) else {}
    req = {
        "token": token,
        "authorization_token": token,
        "payload_digest": plan.digest,
        "attempt_id": attempt.id,
        "outbox_id": attempt.outbox_id,
        "recipient": recipient,
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


def _resolve_governor_config(service: BusinessService, scope: Scope) -> dict[str, Any] | None:
    try:
        cfg = service.resolve_config(scope)
        if cfg and cfg.governor:
            return cfg.governor
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("Governor config resolution fallback for %s: %s", scope, exc, exc_info=True)
    return None


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
        service.store.update_attempt_state(attempt.id, state="unknown", error_code=str(ex))
        service.store.governor_complete_attempt(account, attempt.id, submitted=True, finished_at_ms=now_ms)
        return [Receipt(id=attempt.id, state="unknown", revision_id=revision_id, reason=str(ex))]


async def _execute_with_native_transport(
    service: BusinessService,
    account: AccountKey,
    attempt: Any,
    revision_id: str,
    req: dict[str, Any],
    now_ms: int,
) -> list[Receipt]:
    from gateway.business.worker import delivery_adapter

    try:
        runner = getattr(service, "_transport_runner", None) or getattr(service, "runner", None)
        adapter = delivery_adapter(runner, account)
        if adapter is None:
            raise ConnectionError("business_transport_unavailable")
        resp = await adapter.dispatch_business_part(req)
        if isinstance(resp, dict) and resp.get("ok") is True:
            msg_id = resp.get("message_id") or resp.get("messageId")
            pids = (str(msg_id),) if msg_id else ()
            service.store.update_attempt_state(attempt.id, state="sent", platform_ids=pids)
            service.store.governor_complete_attempt(account, attempt.id, submitted=True, finished_at_ms=now_ms)
            return [Receipt(id=attempt.id, state="sent", revision_id=revision_id, reason=None)]
        error = resp.get("error", "dispatch_error") if isinstance(resp, dict) else "dispatch_failed"
        submitted = not isinstance(resp, dict) or resp.get("submitted") is not False
        state = "unknown" if submitted else "failed"
        service.store.update_attempt_state(attempt.id, state=state, error_code=error)
        service.store.governor_complete_attempt(account, attempt.id, submitted=submitted, finished_at_ms=now_ms)
        return [Receipt(id=attempt.id, state=state, revision_id=revision_id, reason=error)]
    except TimeoutError as ex:
        service.store.update_attempt_state(attempt.id, state="unknown", error_code="timeout")
        service.store.governor_complete_attempt(account, attempt.id, submitted=True, finished_at_ms=now_ms)
        return [Receipt(id=attempt.id, state="unknown", revision_id=revision_id, reason=str(ex))]
    except Exception as ex:  # health: allow BLE001 -- native transport dispatch failures must be recorded as failed attempts
        logger.warning("Native dispatch error for %s: %s", attempt.id, ex, exc_info=True)
        service.store.update_attempt_state(attempt.id, state="unknown", error_code=str(ex))
        service.store.governor_complete_attempt(account, attempt.id, submitted=True, finished_at_ms=now_ms)
        return [Receipt(id=attempt.id, state="unknown", revision_id=revision_id, reason=str(ex))]

async def send_operator_notice(service, scope: Scope, text: str, *, now_ms: int | None = None) -> dict:
    """A verified configured operator has a bounded, paced notice lane."""
    import base64
    import hashlib
    import hmac
    import json
    from gateway.business.dispatch import _default_transport_key
    from gateway.business.worker import delivery_adapter
    from gateway.whatsapp_identity import to_whatsapp_jid
    cfg = service.resolve_config(scope)
    if not cfg or scope.contact_id not in {to_whatsapp_jid(item) for item in cfg.operators}:
        raise BusinessDenied()
    now = int(time.time() * 1000) if now_ms is None else now_ms
    account = AccountKey(scope.transport_profile, scope.account_id)
    adapter = delivery_adapter(getattr(service, "runner", None) or getattr(service, "_transport_runner", None), account)
    if adapter is None:
        return {"ok": False, "error": "business_transport_unavailable"}
    attempt_id = f"operator-{uuid.uuid4()}"
    notice_cfg = {
        "timezone": "UTC", "windows": {"operator": [{"weekdays": list(range(7)), "start": "00:00", "end": "24:00"}]},
        "min_gap_seconds": 2, "caps": {"operator": {"per_hour": 60, "per_day": 200}}, "hard_max_per_day": 1000,
    }
    eligibility = service.store.governor_reserve_attempt(account, attempt_id, message_class="operator", now_ms=now, config=notice_cfg)
    if not eligibility.allowed:
        return {"ok": False, "error": eligibility.reason}
    part = {"index": 0, "operation": "text", "payload": {"text": text}, "media_hash": None}
    digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    payload = {"business_dispatch": 1, "operator_notice": 1, "attempt_id": attempt_id, "outbox_id": attempt_id,
               "part_index": 0, "generation": 1, "plan_id": attempt_id, "plan_digest": digest, "part": part,
               **asdict(scope), "expires_at_ms": now + 30_000}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + hmac.new(_default_transport_key(service.store.db_path.parent), raw, hashlib.sha256).hexdigest()
    request = {"token": token, "authorization_token": token, "attempt_id": attempt_id, "scope": asdict(scope),
               "part": part, "plan_id": attempt_id, "plan_digest": digest, "payload_digest": digest}
    try:
        return await adapter.dispatch_business_part(request)
    finally:
        service.store.governor_complete_attempt(account, attempt_id, submitted=True, finished_at_ms=now)


def resolve_transport_target(store, scope: Scope) -> str:
    """Resolve the canonical contact to one unambiguous receiving transport alias."""
    with store._connect() as db:
        rows = db.execute("SELECT transport_id,held FROM business_aliases WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                          tuple(asdict(scope).values())).fetchall()
    if any(row["held"] for row in rows):
        raise BusinessConflict("contact_alias_held")
    aliases = {row["transport_id"] for row in rows}
    if not aliases and "@" in scope.contact_id:
        return scope.contact_id
    from gateway.whatsapp_identity import to_whatsapp_jid
    for suffix in ("@g.us", "@s.whatsapp.net", "@lid"):
        values = {to_whatsapp_jid(alias) for alias in aliases if alias.endswith(suffix)}
        if len(values) > 1:
            raise BusinessConflict("ambiguous_transport_target")
        if values:
            return next(iter(values))
    values = {to_whatsapp_jid(alias) for alias in aliases if alias.removeprefix("+").isdigit()}
    if len(values) == 1:
        return next(iter(values))
    raise BusinessConflict("transport_target_unavailable")
