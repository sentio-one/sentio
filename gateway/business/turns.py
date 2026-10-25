"""Runner turn integration and shadow decision extraction for WhatsApp business mode."""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.auxiliary_client import async_call_llm
from gateway.business.compose import _extract_json_payload
from gateway.business.decision import decision_from_result
from gateway.business.ingress import _classify, _resolve
from gateway.business.models import (
    BusinessProtocolError,
    ContextSnapshot,
    EpisodeRef,
    Receipt,
    Scope,
    WorkDecision,
    parse_decision,
)

logger = logging.getLogger(__name__)


async def process_business_turn_output(
    runner: Any, event: Any, source: Any, agent_result: dict[str, Any],
    response: str, footer_line: str, intentional_silence: bool,
) -> tuple[str, str, bool]:
    """Apply business supervision to a completed turn: record receipt, suppress supervised text."""
    await handle_business_turn(runner, event, agent_result)
    if is_business_mode_active(runner, source):
        footer_line = ""
        svc = getattr(runner, "_business_service", None)
        cfg = svc.resolve_config(_source_scope(source)) if svc else None
        if cfg and cfg.mode == "supervised":
            intentional_silence = True
            response = ""
    return response, footer_line, intentional_silence


async def shadow_decision(
    snapshot: ContextSnapshot,
    legacy_result: dict[str, Any],
    *,
    main_runtime: dict[str, Any],
) -> WorkDecision:
    """Extract a tool-free WorkDecision for shadow evaluation from context and legacy result."""
    if legacy_result.get("failed") is True or bool(legacy_result.get("error")):
        return decision_from_result(legacy_result)

    if "decision" in legacy_result:
        dec = legacy_result["decision"]
        if isinstance(dec, WorkDecision):
            return dec
        if isinstance(dec, dict):
            return parse_decision(dec)

    # Check for intentional silence in legacy result
    final_response = str(legacy_result.get("final_response") or "").strip()
    if legacy_result.get("silent") is True or not final_response or final_response in ("[SILENCE]", "NO_REPLY", "(empty)"):
        return WorkDecision(
            kind="no_reply",
            reason="legacy_silence",
            intent="",
            fact_ids=(),
            action_ids=(),
            policy_version=snapshot.policy_version,
        )

    # Auxiliary decision extraction
    system_prompt = (
        "You are an auxiliary business decision analyzer. Your job is to extract or determine "
        "the business action decision from the customer conversation context and the agent's proposed response. "
        "Output ONLY a valid JSON object matching the WorkDecision schema:\n"
        '{"kind": "compose" | "no_reply" | "escalate", "reason": "...", "intent": "...", "fact_ids": [...], "action_ids": [...]}'
    )

    customer_events = [
        {"actor": e.actor, "kind": e.kind, "text": e.text}
        for e in snapshot.events
    ]
    verified_facts = [{"id": f.id, "text": f.text} for f in snapshot.facts]

    user_payload = {
        "events": customer_events,
        "legacy_final_response": final_response,
        "verified_facts": verified_facts,
        "rules": list(snapshot.rules),
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]

    try:
        raw_response = await async_call_llm(
            task="business_compose",
            main_runtime=main_runtime,
            messages=messages,
            tools=[],
            timeout=60,
            max_tokens=2048,
        )
        data = _extract_json_payload(raw_response)
        kind = data.get("kind", "compose")
        if kind not in ("compose", "no_reply", "escalate"):
            kind = "compose"
        reason = str(data.get("reason", "shadow_extracted_decision"))
        intent = str(data.get("intent", final_response))
        fact_ids = tuple(str(x) for x in data.get("fact_ids", ()))
        action_ids = tuple(str(x) for x in data.get("action_ids", ()))
        return WorkDecision(
            kind=kind,
            reason=reason,
            intent=intent,
            fact_ids=fact_ids,
            action_ids=action_ids,
            policy_version=snapshot.policy_version,
        )
    except Exception as exc:
        logger.warning("Shadow decision auxiliary call failed: %s", exc, exc_info=True)
        return WorkDecision(
            kind="failed",
            reason=f"shadow_decision_error: {exc}",
            intent="",
            fact_ids=(),
            action_ids=(),
            policy_version=snapshot.policy_version,
        )


def is_business_mode_active(runner: Any, source: Any) -> bool:
    """Return True if native WhatsApp business mode is enabled for *source*."""
    service = getattr(runner, "_business_service", None)
    if service is None or source is None:
        return False
    platform = getattr(source, "platform", None)
    platform_name = getattr(platform, "value", str(platform))
    if platform_name != "whatsapp":
        return False
    cfg = service.resolve_config(_source_scope(source))
    return bool(cfg and cfg.enabled)


def _source_scope(source):
    from gateway.session_identity import identity_of
    identity = identity_of(source)
    runtime = identity.runtime_profile if identity else getattr(source, "profile", None) or "default"
    transport = identity.transport_profile if identity else runtime
    return Scope(runtime, transport, transport, "")


def business_turn_input(runner: Any, event: Any, episode: EpisodeRef, policy: Any) -> str:
    """Format supervised outcome schema and policy notes for the working agent's task input."""
    return (
        f"### BUSINESS MODE SUPERVISED TURN (Episode: {episode.id}, Context: v{episode.context_version})\n"
        "You are operating in WhatsApp supervised business mode. Your response will be reviewed before "
        "anything is sent to the customer.\n"
        "Conclude your turn with a structured decision in JSON matching the WorkDecision schema:\n"
        '```json\n{"kind": "compose", "reason": "...", "intent": "...", "fact_ids": [], "action_ids": [], '
        f'"policy_version": {json.dumps(policy.version)}' + '}\n```\n'
        'Use kind "no_reply" or "escalate" instead when appropriate.\n'
        f"Active Policy Version: {policy.version}\n"
        f"Applicable Rules:\n" + "\n".join(f"- {r}" for r in policy.rules)
    )


async def handle_business_turn(
    runner: Any,
    event: Any,
    result: dict[str, Any],
) -> Receipt | None:
    """Handle one completed agent turn under business supervision.

    Returns None for unconfigured/personal mode; never falls back to None on business failures.
    """
    service = getattr(runner, "_business_service", None)
    if service is None or event is None:
        return None

    resolved = _resolve(runner, event)
    if resolved is None:
        return None

    store, scope, source_id = resolved
    config = service.resolve_config(scope)
    if config is None or config.mode == "off":
        return None

    episode = store.episode_for_source(scope, source_id)
    if episode is None:
        episode = store.record_ingress(scope, _classify(event, source_id), source_id=source_id)

    main_runtime = (
        getattr(event, "main_runtime", None)
        or result.get("main_runtime")
        or getattr(runner, "_main_runtime", {})
        or {}
    )

    try:
        return await service.handle_turn(scope, episode, result, main_runtime=main_runtime)
    except Exception as exc:
        logger.error("Configured business turn handling failed: %s", exc, exc_info=True)
        store.record_verdict(scope, episode.id, kind="failed", reason=str(exc))
        store.put_task(scope, dedupe_key=f"episode:{episode.id}", reason=str(exc), refs=(episode.id,))
        import uuid

        return Receipt(id=str(uuid.uuid4()), state="failed", revision_id=None, reason=str(exc))
