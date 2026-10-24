"""Tests for dedicated customer-message composition from verified context (Task A5)."""

import json
import pytest
from types import SimpleNamespace

from gateway.business.models import (
    ActionReceipt,
    BusinessProtocolError,
    Candidate,
    ContextEvent,
    ContextSnapshot,
    EpisodeRef,
    Fact,
    Scope,
    WorkDecision,
)
from gateway.business.decision import decision_from_result
from gateway.business.compose import compose_candidate, compose_draft
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


def _scope(contact="contact-1", profile="default"):
    return Scope(profile, "whatsapp", "account-1", contact)


def _snapshot(scope, episode, facts=(), actions=()):
    return ContextSnapshot(
        id="snap-1",
        scope=scope,
        episode=episode,
        policy_version="v1",
        lesson_version=0,
        events=(ContextEvent("ev-1", "message", "customer", "Please cancel my plan", None, "inbound"),),
        facts=tuple(facts),
        actions=tuple(actions),
        rules=("Never promise refunds",),
        review_criteria=("Check tone",),
        confirmed_lessons=(),
        missing=(),
    )


@pytest.mark.asyncio
async def test_failed_result_never_calls_composer(store, monkeypatch):
    billing_text = "Internal error: billing system unreachable"
    decision = decision_from_result({"failed": True, "final_response": billing_text})
    assert decision.kind == "failed"

    socket_calls = []
    llm_calls = []

    async def fake_llm(*args, **kwargs):
        llm_calls.append(kwargs)
        return "{}"

    monkeypatch.setattr("gateway.business.compose.async_call_llm", fake_llm)

    scope = _scope()
    episode = store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="s1")
    snapshot = _snapshot(scope, episode)

    receipt, rev = await compose_draft(store, snapshot, decision, main_runtime={"profile": "default"})
    assert receipt.state == "failed"
    assert rev is None
    assert llm_calls == []
    assert socket_calls == []


@pytest.mark.asyncio
async def test_composer_receives_offer_and_verified_action(monkeypatch):
    observed_call = {}
    socket_calls = []

    successful_removal_id = "act-remove-plan-1"
    offer_id = "fact-standard-plan-1"

    async def fake_llm(**kwargs):
        observed_call.update(kwargs)
        return json.dumps({
            "kind": "text",
            "text": "Your plan has been cancelled as requested.",
            "data": {},
            "fact_ids": [offer_id],
            "completed_action_ids": [successful_removal_id],
            "credential_slots": [],
        })

    monkeypatch.setattr("gateway.business.compose.async_call_llm", fake_llm)

    scope = _scope()
    episode = EpisodeRef("ep-1", 1)
    fact = Fact(offer_id, "Standard Plan: R299/mo", "catalog", "v1")
    action = ActionReceipt(successful_removal_id, "cancel_subscription", "sub_123", "success", "tx_999")

    snapshot = _snapshot(scope, episode, facts=(fact,), actions=(action,))
    decision = WorkDecision(
        kind="compose",
        reason="customer request",
        intent="Confirm plan cancellation",
        fact_ids=(offer_id,),
        action_ids=(successful_removal_id,),
        policy_version="v1",
    )

    candidate = await compose_candidate(snapshot, decision, main_runtime={"profile": "default"})

    assert observed_call["tools"] == []
    assert observed_call["timeout"] == 60
    assert observed_call["max_tokens"] == 2048
    assert candidate.completed_action_ids == (successful_removal_id,)
    assert socket_calls == []


@pytest.mark.asyncio
async def test_invalid_output_and_missing_context_escalate(store, monkeypatch):
    socket_calls = []

    # 1. Non-JSON string from LLM
    async def fake_llm_broken(**kwargs):
        return "I think we should say: Hello customer!"

    monkeypatch.setattr("gateway.business.compose.async_call_llm", fake_llm_broken)

    scope = _scope()
    episode = store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="s1")
    snapshot = _snapshot(scope, episode)
    decision = WorkDecision("compose", "reason", "say hi", (), (), "v1")

    invalid_result, rev = await compose_draft(store, snapshot, decision, main_runtime={"profile": "default"})
    assert invalid_result.state == "failed"
    assert rev is None
    assert socket_calls == []

    # 2. Missing unverified fact reference in decision
    bad_decision = WorkDecision("compose", "reason", "say hi", ("unverified_fact",), (), "v1")
    with pytest.raises(BusinessProtocolError):
        await compose_candidate(snapshot, bad_decision, main_runtime={"profile": "default"})


@pytest.mark.asyncio
async def test_stale_composition_is_discarded(store, monkeypatch):
    socket_calls = []

    async def fake_llm(**kwargs):
        return json.dumps({
            "kind": "text",
            "text": "Hello!",
            "data": {},
            "fact_ids": [],
            "completed_action_ids": [],
            "credential_slots": [],
        })

    monkeypatch.setattr("gateway.business.compose.async_call_llm", fake_llm)

    scope = _scope()
    episode = store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="s1")
    snapshot = _snapshot(scope, episode)
    decision = WorkDecision("compose", "reason", "say hi", (), (), "v1")

    # Advance episode context in store so snapshot.episode is now stale
    store.record_ingress(scope, ContextEvent("e2", "message", "customer", "Another message", None, "inbound"), source_id="s2")

    stale_result, rev = await compose_draft(store, snapshot, decision, main_runtime={"profile": "default"})
    assert stale_result.state == "superseded"
    assert rev is None
    assert socket_calls == []


@pytest.mark.asyncio
async def test_failure_shapes_and_cross_profile_isolation(monkeypatch):
    observed_runtimes = []

    async def fake_llm_scalar(**kwargs):
        observed_runtimes.append(kwargs.get("main_runtime", {}).get("profile"))
        return "42"

    monkeypatch.setattr("gateway.business.compose.async_call_llm", fake_llm_scalar)

    scope_a = _scope(profile="profile-a")
    snapshot_a = _snapshot(scope_a, EpisodeRef("ep-a", 1))
    decision = WorkDecision("compose", "reason", "intent", (), (), "v1")

    with pytest.raises(BusinessProtocolError):
        await compose_candidate(snapshot_a, decision, main_runtime={"profile": "profile-a"})

    scope_b = _scope(profile="profile-b")
    snapshot_b = _snapshot(scope_b, EpisodeRef("ep-b", 1))
    with pytest.raises(BusinessProtocolError):
        await compose_candidate(snapshot_b, decision, main_runtime={"profile": "profile-b"})

    assert observed_runtimes == ["profile-a", "profile-b"]

    # Test truncated/empty output
    async def fake_llm_empty(**kwargs):
        return ""

    monkeypatch.setattr("gateway.business.compose.async_call_llm", fake_llm_empty)
    with pytest.raises(BusinessProtocolError):
        await compose_candidate(snapshot_a, decision, main_runtime={"profile": "profile-a"})
