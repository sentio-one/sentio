"""Tests for deterministic checks, advisory review, and bounded revision orchestration (Task A6)."""

import json
import pytest

from gateway.business.models import (
    BusinessConflict,
    Candidate,
    CheckReport,
    ContextEvent,
    ContextSnapshot,
    DraftRevision,
    EpisodeRef,
    Fact,
    ReviewVerdict,
    Scope,
    WorkDecision,
)
from gateway.business.policy import PolicyBundle
from gateway.business.review import prepare_draft, review_candidate
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


def _scope(contact="contact-1", profile="default"):
    return Scope(profile, "whatsapp", "account-1", contact)


def _policy(missing=()):
    return PolicyBundle(
        id="policy-1",
        version="v1",
        facts=(),
        actions=(),
        rules=("Be helpful and accurate",),
        criteria=("Check accuracy",),
        missing=tuple(missing),
    )


def _snapshot(scope, episode):
    return ContextSnapshot(
        id="snap-1",
        scope=scope,
        episode=episode,
        policy_version="v1",
        lesson_version=0,
        events=(ContextEvent("ev-1", "message", "customer", "Hi", None, "inbound"),),
        facts=(),
        actions=(),
        rules=("Be helpful and accurate",),
        review_criteria=("Check accuracy",),
        confirmed_lessons=(),
        missing=(),
    )


@pytest.mark.asyncio
async def test_two_rewrites_then_attention(store, monkeypatch):
    scope = _scope()
    episode = store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="s1")
    snapshot = _snapshot(scope, episode)
    decision = WorkDecision("compose", "reason", "say hi", (), (), "v1")

    compose_call_count = 0

    async def fake_llm(**kwargs):
        nonlocal compose_call_count
        task = kwargs.get("task")
        if task == "business_compose":
            compose_call_count += 1
            return json.dumps({
                "kind": "text",
                "text": f"Draft number {compose_call_count}",
                "data": {},
                "fact_ids": [],
                "completed_action_ids": [],
                "credential_slots": [],
            })
        if task == "business_review":
            return json.dumps({
                "recommendation": "revise",
                "reasons": ["Make it more friendly"],
                "policy_refs": [],
            })
        raise ValueError(f"unexpected task: {task}")

    monkeypatch.setattr("gateway.business.compose.async_call_llm", fake_llm)
    monkeypatch.setattr("gateway.business.review.async_call_llm", fake_llm)

    async def fake_check(*args, **kwargs):
        return CheckReport(ok=True, issues=(), policy_version="v1")

    monkeypatch.setattr("gateway.business.review.check_candidate", fake_check)

    final_receipt = await prepare_draft(
        store, snapshot, decision, policy=_policy(), main_runtime={"profile": "default"}
    )

    saved_revisions = store.list_revisions(scope, episode.id)

    assert compose_call_count == 3  # initial plus two rewrites
    assert len(saved_revisions) == 3
    assert final_receipt.state == "attention"


@pytest.mark.asyncio
async def test_reviewer_timeout_is_unavailable(monkeypatch):
    scope = _scope()
    episode = EpisodeRef("ep-1", 1)
    snapshot = _snapshot(scope, episode)
    candidate = Candidate("text", "Hello", {}, (), (), ())
    revision = DraftRevision("rev-1", scope, episode, 1, candidate, "v1", 0, None, None)

    async def fake_llm_timeout(**kwargs):
        raise TimeoutError("LLM call timed out after 60s")

    monkeypatch.setattr("gateway.business.review.async_call_llm", fake_llm_timeout)

    unavailable = await review_candidate(snapshot, revision, main_runtime={"profile": "default"})
    assert unavailable.recommendation == "unavailable"
    assert unavailable.error_code == "timeout"


@pytest.mark.asyncio
async def test_policy_failure_and_late_review_do_not_authorize(store, monkeypatch):
    scope = _scope()
    episode = store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="s1")
    snapshot = _snapshot(scope, episode)
    decision = WorkDecision("compose", "reason", "say hi", (), (), "v1")

    approval_count = 0

    # 1. Required policy failure
    async def fake_compose_llm(**kwargs):
        return json.dumps({
            "kind": "text",
            "text": "Hello customer",
            "data": {},
            "fact_ids": [],
            "completed_action_ids": [],
            "credential_slots": [],
        })

    monkeypatch.setattr("gateway.business.compose.async_call_llm", fake_compose_llm)

    async def fake_check_missing(*args, **kwargs):
        return CheckReport(ok=False, issues=({"code": "policy_unavailable", "policy": "sales-ops"},), policy_version="v1")

    monkeypatch.setattr("gateway.business.review.check_candidate", fake_check_missing)

    required_failure_receipt = await prepare_draft(
        store, snapshot, decision, policy=_policy(missing=["sales-ops"]), main_runtime={"profile": "default"}
    )
    assert required_failure_receipt.state == "blocked"

    # 2. Late review against human edit / context advance
    episode_2 = store.record_ingress(scope, ContextEvent("e2", "message", "customer", "Second conversation", None, "inbound"), source_id="s2")
    cand = Candidate("text", "Initial draft", {}, (), (), ())
    human_revision = store.append_revision(
        scope, episode_2, cand, expected_revision_id=None, policy_version="v1", lesson_version=0
    )

    # Human sends an inbound or owner reply, bumping context version from 1 to 2 on episode_2
    store.record_ingress(scope, ContextEvent("e3", "message", "customer", "Wait, nevermind", None, "inbound"), source_id="s3")

    late_verdict = ReviewVerdict(recommendation="approve", reasons=(), policy_refs=(), error_code=None)
    with pytest.raises(BusinessConflict):
        store.attach_review(scope, human_revision.id, verdict=late_verdict, expected_context_version=1)

    refreshed_rev = store.get_revision(scope, human_revision.id)
    assert refreshed_rev.review is None  # late prior-version verdict did not attach
    assert approval_count == 0


@pytest.mark.asyncio
async def test_no_rewrite_after_working_turn_failure(store, monkeypatch):
    scope = _scope()
    episode = store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="s1")
    snapshot = _snapshot(scope, episode)
    decision = WorkDecision("failed", "tool execution crashed", "", (), (), "v1")

    compose_calls = 0

    async def fake_compose(**kwargs):
        nonlocal compose_calls
        compose_calls += 1
        return "{}"

    monkeypatch.setattr("gateway.business.compose.async_call_llm", fake_compose)

    receipt = await prepare_draft(store, snapshot, decision, policy=_policy(), main_runtime={"profile": "default"})
    assert receipt.state == "failed"
    assert compose_calls == 0
    assert len(store.list_revisions(scope, episode.id)) == 0

