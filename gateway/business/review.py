"""Deterministic checks, advisory auxiliary review, and bounded revision orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict
from typing import Any

from agent.auxiliary_client import async_call_llm
from gateway.business.compose import _extract_json_payload, compose_candidate
from gateway.business.models import (
    BusinessConflict,
    Candidate,
    ContextSnapshot,
    DraftRevision,
    Receipt,
    ReviewVerdict,
    WorkDecision,
)
from gateway.business.policy import PolicyBundle, check_candidate
from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)


async def review_candidate(
    snapshot: ContextSnapshot,
    revision: DraftRevision,
    *,
    main_runtime: dict[str, Any],
) -> ReviewVerdict:
    """Evaluate a drafted candidate against rules and tone using business_review."""
    system_prompt = (
        "You are an auxiliary business message reviewer. Evaluate the customer-facing message "
        "candidate against the conversation context, rules, and review criteria. "
        "Recommend exactly one of: approve, revise, reject, unavailable. "
        "Output ONLY a valid JSON object matching:\n"
        '{"recommendation": "approve|revise|reject|unavailable", "reasons": ["..."], "policy_refs": []}'
    )

    user_payload = {
        "scope": asdict(snapshot.scope),
        "episode": asdict(snapshot.episode),
        "events": [asdict(event) for event in snapshot.events],
        "confirmed_lessons": list(snapshot.confirmed_lessons),
        "lesson_version": snapshot.lesson_version,
        "policy_version": snapshot.policy_version,
        "missing": list(snapshot.missing),
        "actions": [asdict(action) for action in snapshot.actions],
        "candidate": {"kind": revision.candidate.kind, "text": revision.candidate.text, "data": revision.candidate.data},
        "rules": list(snapshot.rules),
        "review_criteria": list(snapshot.review_criteria),
        "facts": [{"id": f.id, "text": f.text} for f in snapshot.facts if f.id in revision.candidate.fact_ids],
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]

    try:
        raw = await async_call_llm(
            task="business_review",
            main_runtime=main_runtime,
            messages=messages,
            tools=[],
            timeout=60,
            max_tokens=2048,
        )
        data = _extract_json_payload(raw)
        rec = str(data.get("recommendation") or "").strip().lower()
        if rec not in {"approve", "revise", "reject", "unavailable"}:
            return ReviewVerdict(recommendation="unavailable", reasons=("invalid_recommendation",), policy_refs=(), error_code="schema_violation")
        reasons = tuple(str(r) for r in data.get("reasons", ()))
        policy_refs = tuple(str(p) for p in data.get("policy_refs", ()))
        return ReviewVerdict(recommendation=rec, reasons=reasons, policy_refs=policy_refs, error_code=None)
    except TimeoutError:
        return ReviewVerdict(recommendation="unavailable", reasons=("review timed out",), policy_refs=(), error_code="timeout")
    except Exception as exc:
        logger.warning("Advisory review failed: %s", exc, exc_info=True)
        code = "timeout" if "timeout" in str(exc).lower() else "error"
        return ReviewVerdict(recommendation="unavailable", reasons=(str(exc),), policy_refs=(), error_code=code)


async def prepare_draft(
    store: BusinessStore,
    snapshot: ContextSnapshot,
    decision: WorkDecision,
    *,
    policy: PolicyBundle,
    main_runtime: dict[str, Any],
    rewrite_attempts: int = 2,
    initial_candidate: Candidate | None = None,
) -> Receipt:
    """Orchestrate initial composition, checks, advisory review, and up to two rewrites."""
    if decision.kind != "compose":
        receipt_id = str(uuid.uuid4())
        return Receipt(id=receipt_id, state=decision.kind, revision_id=None, reason=decision.reason)

    rewrite_budget = min(2, max(0, rewrite_attempts))
    last_candidate = None
    last_rev_id: str | None = None
    reasons: tuple[str, ...] = ()

    for attempt in range(rewrite_budget + 1):
        try:
            candidate = initial_candidate if attempt == 0 and initial_candidate is not None else await compose_candidate(
                snapshot, decision, main_runtime=main_runtime, previous=last_candidate, reasons=reasons
            )
        except Exception as exc:
            logger.warning("Composition failed: %s", exc, exc_info=True)
            receipt_id = str(uuid.uuid4())
            store.put_task(snapshot.scope, dedupe_key=f"episode:{snapshot.episode.id}", reason="composition_failed", refs=(snapshot.episode.id,))
            return Receipt(id=receipt_id, state="failed", revision_id=None, reason=str(exc))

        try:
            revision = store.append_revision(
                snapshot.scope,
                snapshot.episode,
                candidate,
                expected_revision_id=last_rev_id,
                policy_version=policy.version,
                lesson_version=snapshot.lesson_version,
            )
        except BusinessConflict as exc:
            state = "superseded" if exc.args[0] == "stale_episode" else "conflict"
            return Receipt(id=str(uuid.uuid4()), state=state, revision_id=None, reason=str(exc))

        last_candidate = candidate
        last_rev_id = revision.id

        # Deterministic checks
        check_report = await check_candidate(snapshot.scope, candidate, bundle=policy)
        try:
            revision = store.attach_checks(
                snapshot.scope, revision.id, checks=check_report, expected_context_version=snapshot.episode.context_version
            )
        except BusinessConflict:
            return Receipt(id=str(uuid.uuid4()), state="superseded", revision_id=None, reason="stale_episode")

        if not check_report.ok:
            if any(issue.get("code") == "policy_unavailable" for issue in check_report.issues):
                store.put_task(snapshot.scope, dedupe_key=f"revision:{revision.id}", reason="policy_unavailable", refs=(revision.id,))
                return Receipt(id=str(uuid.uuid4()), state="blocked", revision_id=revision.id, reason="policy_unavailable")
            if attempt < rewrite_budget:
                reasons = tuple(issue.get("reason", issue.get("code", "check_failed")) for issue in check_report.issues)
                continue
            store.put_task(snapshot.scope, dedupe_key=f"revision:{revision.id}", reason="checks_failed", refs=(revision.id,))
            return Receipt(id=str(uuid.uuid4()), state="attention", revision_id=revision.id, reason="checks_failed")

        # Advisory review
        verdict = await review_candidate(snapshot, revision, main_runtime=main_runtime)
        try:
            revision = store.attach_review(
                snapshot.scope, revision.id, verdict=verdict, expected_context_version=snapshot.episode.context_version
            )
        except BusinessConflict:
            return Receipt(id=str(uuid.uuid4()), state="superseded", revision_id=None, reason="stale_episode")

        if verdict.recommendation == "revise" and attempt < rewrite_budget:
            reasons = verdict.reasons or ("reviewer_requested_revision",)
            continue
        if verdict.recommendation == "revise":
            store.put_task(snapshot.scope, dedupe_key=f"revision:{revision.id}", reason="exhausted_rewrites", refs=(revision.id,))
            return Receipt(id=str(uuid.uuid4()), state="attention", revision_id=revision.id, reason="exhausted_rewrites")
        if verdict.recommendation in ("unavailable", "reject"):
            reason = f"review_{verdict.recommendation}"
            store.put_task(snapshot.scope, dedupe_key=f"revision:{revision.id}", reason=reason, refs=(revision.id,))
            return Receipt(id=str(uuid.uuid4()), state="attention", revision_id=revision.id, reason=reason)
        if verdict.recommendation == "approve":
            return Receipt(id=str(uuid.uuid4()), state="held", revision_id=revision.id, reason=None)

    return Receipt(id=str(uuid.uuid4()), state="attention", revision_id=last_rev_id, reason="loop_exhausted")
