"""Offline acceptance: real service, operator operations, outbox, governor and receipts.

Only the model, policy plugin and network boundary are faked; every state transition
below is produced by production code against a real SQLite store.
"""

from __future__ import annotations

import json

import pytest

from gateway.business.config import BusinessConfig
from gateway.business.context import build_context
from gateway.business.control import execute_business_request
from gateway.business.learning import confirm_lesson, propose_lesson
from gateway.business.models import AccountKey, CheckReport, ContextEvent, Principal, Scope
from gateway.business.policy import PolicyBundle
from gateway.business.receipts import DeliveryEvent, apply_delivery_event
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.business.transport import dispatch_due

NOW = 1_000_000
GOVERNOR = {
    "timezone": "UTC",
    "windows": {k: [{"weekdays": list(range(7)), "start": "00:00", "end": "24:00"}] for k in ("cold", "reply", "followup")},
    "min_gap_seconds": 10,
    "caps": {},
    "hard_max_per_day": 100,
}
POLICY = PolicyBundle("sales", "v1", (), (), ("Use verified facts",), (), ())
CAPABILITIES = frozenset({"admin", "review", "approve", "operator", "propose_lessons", "confirm_lesson"})


def _operator(runtime="default"):
    return Principal("operator-1", runtime, CAPABILITIES, "human")


async def _model(*, task, **kwargs):
    if task == "business_compose":
        return {"kind": "text", "text": "Our standard pricing is R500 per user per month.", "data": {},
                "fact_ids": [], "completed_action_ids": [], "credential_slots": []}
    return {"recommendation": "approve", "reasons": [], "policy_refs": []}


@pytest.fixture
def stack(tmp_path, monkeypatch):
    async def policy(*args, **kwargs):
        return POLICY

    async def checks(*args, **kwargs):
        return CheckReport(True, (), "v1")

    monkeypatch.setattr("gateway.business.service.collect_policy", policy)
    monkeypatch.setattr("gateway.business.policy.collect_policy", policy)
    monkeypatch.setattr("gateway.business.review.check_candidate", checks)
    monkeypatch.setattr("gateway.business.policy.check_candidate", checks)
    monkeypatch.setattr("gateway.business.compose.async_call_llm", _model)
    monkeypatch.setattr("gateway.business.review.async_call_llm", _model)
    store = BusinessStore(tmp_path)
    scope = store.bind_contact("default", "default", "default", ["27800000001@s.whatsapp.net"])
    service = BusinessService(store, config_resolver=lambda _: BusinessConfig(
        "supervised", ("sales",), ("operator-1",), 2, 86400, 604800, GOVERNOR))
    return store, service, scope


@pytest.mark.asyncio
async def test_inbound_to_edit_approval_paced_delivery_and_learning(stack):
    store, service, scope = stack
    account = AccountKey(scope.transport_profile, scope.account_id)
    operator = _operator()

    # 1. Inbound message -> agent decision -> composed, checked, reviewed draft held in the outbox.
    episode = store.record_ingress(
        scope, ContextEvent("e1", "message", "customer", "Can I get pricing for 50 users?", None, "inbound"),
        source_id="src-1")
    decision = {"kind": "compose", "reason": "answer", "intent": "quote", "fact_ids": [],
                "action_ids": [], "policy_version": "v1"}
    held = await service.handle_turn(scope, episode, {"final_response": json.dumps(decision)}, main_runtime={})
    assert held.state == "held"
    original_plan = store.get_plan_by_revision(scope, held.revision_id)

    # 2. Operator edits the draft; the first plan can no longer be approved.
    exact_text = "Our tier 2 pricing is R450 per user per month for 50 users."
    edited = await execute_business_request(
        service, operation="operator.edit", principal=operator,
        params={"revision_id": held.revision_id, "text": exact_text})
    assert edited["ok"], edited
    new_revision = edited["revision"]["id"]
    new_plan = store.get_plan_by_revision(scope, new_revision)
    assert new_plan.digest != original_plan.digest

    stale = await execute_business_request(
        service, operation="operator.approve", principal=operator,
        params={"revision_id": held.revision_id, "plan_digest": original_plan.digest})
    assert stale["ok"] is False

    # 3. Approval requires the exact digest of what will be sent.
    wrong = await execute_business_request(
        service, operation="operator.approve", principal=operator,
        params={"revision_id": new_revision, "plan_digest": "sha256:wrong"})
    assert wrong["ok"] is False
    approved = await execute_business_request(
        service, operation="operator.approve", principal=operator,
        params={"revision_id": new_revision, "plan_digest": new_plan.digest})
    assert approved["ok"], approved

    # 4. Physical dispatch sends the approved bytes once and is paced by the governor.
    sent = []

    async def network(request):
        sent.append(request)
        return {"message_id": "wa-1"}

    assert (await dispatch_due(service, account, transport_executor=network, now_ms=NOW))[0].state == "sent"
    assert sent[0]["part"]["payload"]["text"] == exact_text
    assert await dispatch_due(service, account, transport_executor=network, now_ms=NOW + 1) == []
    assert len(sent) == 1

    # 5. The platform receipt reconciles to the outbox.
    apply_delivery_event(store, DeliveryEvent(account, "wa-1", 3, "DELIVERY_ACK", NOW + 2))
    with store._connect() as db:
        assert db.execute("SELECT state FROM business_outbox WHERE revision_id=?", (new_revision,)).fetchone()["state"] == "delivered"

    # 6. A confirmed lesson from the edit reaches the next draft's context.
    store.record_review_event(
        scope, revision_id=new_revision, policy_version="v1", model_version="test-model",
        message_class="reply", outcome="edited_and_approved", agreed=True)
    lesson = propose_lesson(operator, scope, topic="volume_discount",
                            rule="For 50+ users apply Tier 2 volume discount of R450/user",
                            category="pricing", policy_id=POLICY.id, store=store)
    confirm_lesson(operator, scope, lesson_id=lesson.id, store=store)
    next_episode = store.record_ingress(
        scope, ContextEvent("e2", "message", "customer", "Quote for 60 users?", None, "inbound"),
        source_id="src-2")
    context = build_context(store, scope, next_episode, policy=POLICY, lesson_version=1)
    assert any("Tier 2 volume discount" in text for text in context.confirmed_lessons)


@pytest.mark.asyncio
async def test_new_inbound_message_invalidates_pending_approval(stack):
    store, service, scope = stack
    account = AccountKey(scope.transport_profile, scope.account_id)
    episode = store.record_ingress(
        scope, ContextEvent("e1", "message", "customer", "Price?", None, "inbound"), source_id="src-1")
    decision = {"kind": "compose", "reason": "answer", "intent": "quote", "fact_ids": [],
                "action_ids": [], "policy_version": "v1"}
    held = await service.handle_turn(scope, episode, {"final_response": json.dumps(decision)}, main_runtime={})
    plan = store.get_plan_by_revision(scope, held.revision_id)
    approved = await execute_business_request(
        service, operation="operator.approve", principal=_operator(),
        params={"revision_id": held.revision_id, "plan_digest": plan.digest})
    assert approved["ok"], approved

    store.record_ingress(
        scope, ContextEvent("e2", "message", "customer", "Actually, never mind", None, "inbound"), source_id="src-2")

    async def network(request):
        raise AssertionError("an approval from before a new customer message must not be sent")

    assert await dispatch_due(service, account, transport_executor=network, now_ms=NOW) == []
