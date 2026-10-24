"""Dedicated tool-free composition from verified customer context."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from agent.auxiliary_client import async_call_llm
from gateway.business.models import (
    BusinessConflict,
    BusinessProtocolError,
    Candidate,
    ContextSnapshot,
    DraftRevision,
    Receipt,
    WorkDecision,
    parse_candidate,
)
from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)


def _extract_json_payload(raw: Any) -> Any:
    """Extract a JSON object from string, response object, or dict."""
    if isinstance(raw, dict):
        return raw
    text = ""
    if isinstance(raw, str):
        text = raw
    elif hasattr(raw, "content") and isinstance(raw.content, str):
        text = raw.content
    elif hasattr(raw, "choices") and raw.choices:
        choice = raw.choices[0]
        if hasattr(choice, "message") and hasattr(choice.message, "content"):
            text = choice.message.content or ""
    text = text.strip()
    if not text:
        raise BusinessProtocolError("empty_llm_response")
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        data = json.loads(text)
    except Exception as exc:
        raise BusinessProtocolError("invalid_json_response") from exc
    if not isinstance(data, dict):
        raise BusinessProtocolError("json_scalar_response")
    return data


def _validate_references(snapshot: ContextSnapshot, decision: WorkDecision) -> None:
    verified_facts = {f.id for f in snapshot.facts}
    for fid in decision.fact_ids:
        if fid not in verified_facts:
            raise BusinessProtocolError(f"unverified_fact_reference: {fid}")

    action_map = {a.id: a for a in snapshot.actions}
    for aid in decision.action_ids:
        if aid not in action_map:
            raise BusinessProtocolError(f"unverified_action_reference: {aid}")
        action = action_map[aid]
        if action.state not in ("success", "completed", "executed"):
            raise BusinessProtocolError(f"incomplete_action_reference: {aid}")


async def compose_candidate(
    snapshot: ContextSnapshot,
    decision: WorkDecision,
    *,
    main_runtime: dict[str, Any],
    previous: Candidate | None = None,
    reasons: tuple[str, ...] = (),
) -> Candidate:
    """Compose a customer message candidate using a dedicated tool-free auxiliary call."""
    if decision.kind != "compose":
        raise BusinessProtocolError(f"cannot compose decision with kind={decision.kind}")

    _validate_references(snapshot, decision)

    system_prompt = (
        "You are an auxiliary business message composer. Your job is to draft an appropriate "
        "customer message based strictly on verified facts and completed actions. Never invent facts, "
        "prices, or features. Output ONLY a valid JSON object matching the Candidate schema:\n"
        '{"kind": "text", "text": "...", "data": {}, "fact_ids": [...], "completed_action_ids": [...], "credential_slots": []}'
    )

    facts_summary = [{"id": f.id, "text": f.text} for f in snapshot.facts if f.id in decision.fact_ids]
    actions_summary = [{"id": a.id, "kind": a.kind, "subject": a.subject} for a in snapshot.actions if a.id in decision.action_ids]

    user_payload: dict[str, Any] = {
        "intent": decision.intent,
        "verified_facts": facts_summary,
        "completed_actions": actions_summary,
        "rules": list(snapshot.rules),
    }
    if previous is not None:
        user_payload["previous_candidate"] = {"text": previous.text, "kind": previous.kind}
        user_payload["rejection_reasons"] = list(reasons)

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
    except Exception as exc:
        raise BusinessProtocolError(f"auxiliary_call_failed: {exc}") from exc

    payload = _extract_json_payload(raw_response)
    candidate = parse_candidate(payload)

    for fid in candidate.fact_ids:
        if fid not in decision.fact_ids:
            raise BusinessProtocolError(f"candidate_unverified_fact: {fid}")
    for aid in candidate.completed_action_ids:
        if aid not in decision.action_ids:
            raise BusinessProtocolError(f"candidate_unverified_action: {aid}")

    return candidate


async def compose_draft(
    store: BusinessStore,
    snapshot: ContextSnapshot,
    decision: WorkDecision,
    *,
    main_runtime: dict[str, Any],
    previous: Candidate | None = None,
    reasons: tuple[str, ...] = (),
    expected_revision_id: str | None = None,
) -> tuple[Receipt, DraftRevision | None]:
    """Compose a candidate and attempt CAS append in store. Returns (Receipt, Revision | None)."""
    if decision.kind != "compose":
        receipt_id = str(uuid.uuid4())
        return Receipt(id=receipt_id, state=decision.kind, revision_id=None, reason=decision.reason), None

    try:
        candidate = await compose_candidate(
            snapshot, decision, main_runtime=main_runtime, previous=previous, reasons=reasons
        )
    except Exception as exc:
        receipt_id = str(uuid.uuid4())
        return Receipt(id=receipt_id, state="failed", revision_id=None, reason=str(exc)), None

    try:
        revision = store.append_revision(
            snapshot.scope,
            snapshot.episode,
            candidate,
            expected_revision_id=expected_revision_id,
            policy_version=snapshot.policy_version,
            lesson_version=snapshot.lesson_version,
        )
        receipt_id = str(uuid.uuid4())
        return Receipt(id=receipt_id, state="pending", revision_id=revision.id, reason=None), revision
    except BusinessConflict as exc:
        receipt_id = str(uuid.uuid4())
        state = "superseded" if exc.args[0] == "stale_episode" else "conflict"
        return Receipt(id=receipt_id, state=state, revision_id=None, reason=str(exc)), None
