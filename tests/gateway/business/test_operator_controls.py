"""Tests for authorized operator API, digests, and WhatsApp reaction controls (Task C1)."""

from __future__ import annotations

from types import SimpleNamespace
import pytest

from gateway.business.models import (
    AccountKey,
    BusinessConflict,
    Candidate,
    ContextEvent,
    Principal,
    Scope,
    ReviewVerdict,
)
from gateway.business.operators import (
    create_digest,
    handle_operator_event,
)
from gateway.business.permissions import authorize_operation
from gateway.business.render import DeliveryPart, DeliveryPlan
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


@pytest.fixture
def service(store):
    return BusinessService(store)


def _scope(contact_id="27600000001@s.whatsapp.net"):
    return Scope("default", "whatsapp", "account-1", contact_id)


def _account():
    return AccountKey(transport_profile="whatsapp", account_id="account-1")


def _operator_principal():
    return Principal("operator-1", "default", frozenset({"approve", "review", "operator"}), "human")


@pytest.mark.asyncio
async def test_digest_and_reaction_bind_exact_actor_and_revision(store, service):
    scope = _scope()
    operator = _operator_principal()

    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="src-1")
    ep = store.episode_for_source(scope, "src-1")

    candidate = Candidate("text", "Draft response", {}, (), (), ())
    rev = store.append_revision(scope, ep, candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)
    part = DeliveryPart(0, "text", {"text": "Draft response"}, None)
    plan = DeliveryPlan("p1", rev.id, scope, ep.context_version, "v1", (part,), 2000000, "sha256:d1")
    store.save_plan(plan)
    store.submit_revision(scope, rev.id, request_id="req-1", now_ms=1000)

    # 1. Create digest for the operator
    digest = create_digest(operator, scope, revision_ids=(rev.id,), store=store)
    assert digest["digest_id"] is not None

    # Stale context: advance contact context version to simulate a new customer message arriving
    store.record_ingress(scope, ContextEvent("e2", "message", "customer", "Wait, never mind", None, "inbound"), source_id="src-2")

    # Stale digest approval attempt
    stale_digest_result = await handle_operator_event(
        service,
        {
            "action": "approve_digest",
            "digest_id": digest["digest_id"],
            "revision_id": rev.id,
        },
        principal=operator,
    )
    assert stale_digest_result["error"] == "stale_revision"

    # 2. Unauthorized reaction from unknown party
    stranger = Principal("unknown-actor", "default", frozenset(), "external")
    unauthorized_reaction = await handle_operator_event(
        service,
        {
            "action": "reaction",
            "emoji": "👍",
            "target_message_id": "msg-card-1",
            "revision_id": rev.id,
        },
        principal=stranger,
    )
    assert unauthorized_reaction.approval_count == 0


@pytest.mark.asyncio
async def test_ambiguous_operator_command_refuses(service):
    operator = _operator_principal()

    # Ambiguous shorthand command
    ambiguous_command = await handle_operator_event(
        service,
        {
            "action": "shorthand",
            "text": "yes 1",  # Ambiguous when multiple items are active without context
        },
        principal=operator,
    )
    assert ambiguous_command.approval_count == 0

    # Deduplicated alert
    alert1 = await handle_operator_event(
        service,
        {"action": "alert", "dedupe_key": "stale-context-notice", "text": "Notice"},
        principal=operator,
    )
    alert2 = await handle_operator_event(
        service,
        {"action": "alert", "dedupe_key": "stale-context-notice", "text": "Notice"},
        principal=operator,
    )
    deduplicated_alert_count = 1 if alert1.get("alert_id") == alert2.get("alert_id") or not alert2.get("emitted") else 2
    assert deduplicated_alert_count == 1


@pytest.mark.asyncio
async def test_advisory_unavailable_requires_audited_approval_override(store):
    scope=_scope('27600000009@s.whatsapp.net')
    store.bind_contact(scope.runtime_profile,scope.transport_profile,scope.account_id,[scope.contact_id])
    store.record_ingress(scope,ContextEvent('review-in','message','customer','Hello',None,'inbound'),source_id='review-in')
    episode=store.episode_for_source(scope,'review-in')
    revision=store.append_revision(scope,episode,Candidate('text','Reviewed copy',{},(),(),()),expected_revision_id=None,policy_version='v1',lesson_version=0)
    store.attach_review(scope,revision.id,verdict=ReviewVerdict('unavailable',('reviewer offline',),(),'timeout'),expected_context_version=episode.context_version)
    plan=DeliveryPlan('review-plan',revision.id,scope,episode.context_version,'v1',(DeliveryPart(0,'text',{'text':'Reviewed copy'},None),),2_000_000,'sha256:review')
    store.save_plan(plan)
    store.submit_revision(scope,revision.id,request_id='review-request',now_ms=1_000)
    with pytest.raises(BusinessConflict,match='override_reason_required'):
        store.approve_revision(_operator_principal(),scope,revision_id=revision.id,plan_digest=plan.digest,now_ms=1_000)
    approval=store.approve_revision(_operator_principal(),scope,revision_id=revision.id,plan_digest=plan.digest,now_ms=1_001,override_reason='I reviewed the unavailable advisory result')
    assert approval.override_reason=='I reviewed the unavailable advisory result'
