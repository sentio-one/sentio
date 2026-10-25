"""Tests for exact approval and durable outbox state transitions (Task B2)."""

from __future__ import annotations

from pathlib import Path
import pytest

from gateway.business.approvals import (
    Approval,
    approve_revision,
)
from gateway.business.models import (
    AccountKey,
    BusinessConflict,
    BusinessDenied,
    BusinessProtocolError,
    Candidate,
    CheckReport,
    ContextEvent,
    Principal,
    ReviewVerdict,
    Scope,
)
from gateway.business.outbox import (
    Attempt,
    claim_part,
    invalidate_contact,
    reject_revision,
    submit_revision,
)
from gateway.business.render import (
    DeliveryPart,
    DeliveryPlan,
    render_delivery_plan,
)
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


def _scope(contact_id="27600000001@s.whatsapp.net"):
    return Scope("default", "whatsapp", "account-1", contact_id)


def _principal(subject="operator-1"):
    return Principal(subject=subject, runtime_profile="default", operations=frozenset({"approve", "reject"}), kind="human")


def _setup_episode_and_revision(store, scope, *, source_id="src-1", text="Hello customer!", kind="text", data=None, checks=None, review=None):
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent(f"e-{source_id}", "message", "customer", "Hi", None, "inbound"), source_id=source_id)
    ep = store.episode_for_source(scope, source_id)

    rev = store.append_revision(
        scope,
        ep,
        Candidate(kind, text, data or {}, (), (), ()),
        expected_revision_id=None,
        policy_version="v1",
        lesson_version=0,
    )
    if checks:
        rev = store.attach_checks(scope, rev.id, checks=checks, expected_context_version=ep.context_version)
    if review:
        rev = store.attach_review(scope, rev.id, verdict=review, expected_context_version=ep.context_version)
    return ep, rev


@pytest.mark.asyncio
async def test_held_receipt_is_not_send_success(store):
    scope = _scope()
    _ep, rev = _setup_episode_and_revision(store, scope)

    plan = await render_delivery_plan(scope, rev, now_ms=1000000)
    store.save_plan(plan)

    submitted = await submit_revision(store, scope, rev.id, request_id="req-1", now_ms=1000000)
    assert submitted.state == "held"
    assert submitted.revision_id == rev.id
    assert not hasattr(submitted, "message_id")

    # Idempotent re-submission with same request_id returns existing receipt
    re_submitted = await submit_revision(store, scope, rev.id, request_id="req-1", now_ms=1000000)
    assert re_submitted.id == submitted.id
    assert re_submitted.state == "held"


@pytest.mark.asyncio
async def test_approval_is_exact_and_idempotent(store):
    scope = _scope()
    principal = _principal()
    _ep, rev = _setup_episode_and_revision(store, scope)

    plan = await render_delivery_plan(scope, rev, now_ms=1000000)
    store.save_plan(plan)

    await submit_revision(store, scope, rev.id, request_id="req-1", now_ms=1000000)

    # 1. Approval with exact plan digest succeeds
    first_approval = await approve_revision(
        store,
        principal,
        scope,
        revision_id=rev.id,
        plan_digest=plan.digest,
        now_ms=1000000,
    )
    assert first_approval.revision_id == rev.id
    assert first_approval.actor == principal.subject
    assert first_approval.expires_at_ms > 1000000

    # 2. Idempotent approval returns same approval identity
    second_approval = await approve_revision(
        store,
        principal,
        scope,
        revision_id=rev.id,
        plan_digest=plan.digest,
        now_ms=1000050,
    )
    assert second_approval.id == first_approval.id

    # 3. Mismatched or old digest fails with BusinessConflict
    with pytest.raises(BusinessConflict, match="digest_mismatch"):
        await approve_revision(
            store,
            principal,
            scope,
            revision_id=rev.id,
            plan_digest="sha256:wrong_digest_0000000000000000000000000000000000000000000000000",
            now_ms=1000100,
        )


@pytest.mark.asyncio
async def test_late_results_and_suppression_cannot_resurrect_approval(store):
    principal = _principal()

    # Setup scenario 1: New inbound arrives -> contact context changes -> claim_part refuses
    scope1 = _scope("27600000001@s.whatsapp.net")
    account1 = AccountKey(transport_profile=scope1.transport_profile, account_id=scope1.account_id)
    _ep1, rev1 = _setup_episode_and_revision(store, scope1, source_id="src-1")
    plan1 = await render_delivery_plan(scope1, rev1, now_ms=1000000)
    store.save_plan(plan1)
    await submit_revision(store, scope1, rev1.id, request_id="req-1", now_ms=1000000)
    await approve_revision(store, principal, scope1, revision_id=rev1.id, plan_digest=plan1.digest, now_ms=1000000)

    # Inbound message bumps contact context_version
    store.record_ingress(scope1, ContextEvent("e2", "message", "customer", "Wait actually...", None, "inbound"), source_id="src-2")

    after_new_inbound_claim = await claim_part(store, account1, now_ms=1000100)
    after_new_inbound_dispatches = [after_new_inbound_claim] if after_new_inbound_claim else []
    assert after_new_inbound_dispatches == []

    # Setup scenario 2: Contact suppression -> claim_part refuses
    scope2 = _scope("27600000002@s.whatsapp.net")
    account2 = AccountKey(transport_profile=scope2.transport_profile, account_id=scope2.account_id)
    _ep2, rev2 = _setup_episode_and_revision(store, scope2, source_id="src-3")
    plan2 = await render_delivery_plan(scope2, rev2, now_ms=1000000)
    store.save_plan(plan2)
    await submit_revision(store, scope2, rev2.id, request_id="req-2", now_ms=1000000)
    await approve_revision(store, principal, scope2, revision_id=rev2.id, plan_digest=plan2.digest, now_ms=1000000)

    store.set_suppressed(scope2, reason="opt_out", source_id="src-opt-out")

    after_suppression_claim = await claim_part(store, account2, now_ms=1000100)
    after_suppression_dispatches = [after_suppression_claim] if after_suppression_claim else []
    assert after_suppression_dispatches == []

    # Setup scenario 3: Contact invalidation / policy change -> claim_part refuses
    scope3 = _scope("27600000003@s.whatsapp.net")
    account3 = AccountKey(transport_profile=scope3.transport_profile, account_id=scope3.account_id)
    _ep3, rev3 = _setup_episode_and_revision(store, scope3, source_id="src-4")
    plan3 = await render_delivery_plan(scope3, rev3, now_ms=1000000)
    store.save_plan(plan3)
    await submit_revision(store, scope3, rev3.id, request_id="req-3", now_ms=1000000)
    await approve_revision(store, principal, scope3, revision_id=rev3.id, plan_digest=plan3.digest, now_ms=1000000)

    # Explicit invalidation
    await invalidate_contact(store, scope3, reason="policy_change", now_ms=1000100)

    after_policy_change_claim = await claim_part(store, account3, now_ms=1000200)
    after_policy_change_dispatches = [after_policy_change_claim] if after_policy_change_claim else []
    assert after_policy_change_dispatches == []


@pytest.mark.asyncio
async def test_advisory_reject_requires_override_reason(store):
    scope = _scope("27600000004@s.whatsapp.net")
    principal = _principal()
    review_reject = ReviewVerdict(recommendation="reject", reasons=("too_pushy",), policy_refs=(), error_code=None)
    _ep, rev = _setup_episode_and_revision(store, scope, source_id="src-rej", review=review_reject)


    plan = await render_delivery_plan(scope, rev, now_ms=1000000)
    store.save_plan(plan)
    await submit_revision(store, scope, rev.id, request_id="req-rej-1", now_ms=1000000)

    # Approving advisory reject without override reason must fail
    with pytest.raises(BusinessConflict, match="override_reason_required"):
        await approve_revision(
            store,
            principal,
            scope,
            revision_id=rev.id,
            plan_digest=plan.digest,
            override_reason=None,
            now_ms=1000000,
        )

    # Approving with override reason succeeds
    appr = await approve_revision(
        store,
        principal,
        scope,
        revision_id=rev.id,
        plan_digest=plan.digest,
        override_reason="Customer explicitly asked for urgency",
        now_ms=1000000,
    )
    assert appr.override_reason == "Customer explicitly asked for urgency"


@pytest.mark.asyncio
async def test_rejection_marks_outbox_rejected(store):
    scope = _scope()
    principal = _principal()
    _ep, rev = _setup_episode_and_revision(store, scope)

    plan = await render_delivery_plan(scope, rev, now_ms=1000000)
    store.save_plan(plan)
    await submit_revision(store, scope, rev.id, request_id="req-to-reject", now_ms=1000000)

    receipt = await reject_revision(store, principal, scope, revision_id=rev.id, reason="not_appropriate", now_ms=1000000)
    assert receipt.state == "rejected"
    assert receipt.reason == "not_appropriate"

    # Outbox cannot be claimed after rejection
    account = AccountKey(transport_profile=scope.transport_profile, account_id=scope.account_id)
    claim = await claim_part(store, account, now_ms=1000100)
    assert claim is None


@pytest.mark.asyncio
async def test_concurrent_claim_only_one_claim_per_part(store):
    import asyncio

    scope = _scope("27600000005@s.whatsapp.net")
    principal = _principal()
    account = AccountKey(transport_profile=scope.transport_profile, account_id=scope.account_id)
    _ep, rev = _setup_episode_and_revision(store, scope, source_id="src-concurrent")

    plan = await render_delivery_plan(scope, rev, now_ms=1000000)
    store.save_plan(plan)
    await submit_revision(store, scope, rev.id, request_id="req-concurrent", now_ms=1000000)
    await approve_revision(store, principal, scope, revision_id=rev.id, plan_digest=plan.digest, now_ms=1000000)

    # Concurrently claim part
    claims = await asyncio.gather(
        claim_part(store, account, now_ms=1000010),
        claim_part(store, account, now_ms=1000010),
    )
    successful_claims = [c for c in claims if c is not None]
    # Only 1 part was available (plan has 1 text part), so exactly one claim must succeed
    assert len(successful_claims) == 1
    assert successful_claims[0].part_index == 0


@pytest.mark.asyncio
async def test_invalidated_late_result_remains_superseded(store):
    scope = _scope("27600000006@s.whatsapp.net")
    principal = _principal()
    account = AccountKey(transport_profile=scope.transport_profile, account_id=scope.account_id)
    _ep, rev = _setup_episode_and_revision(store, scope, source_id="src-late")


    plan = await render_delivery_plan(scope, rev, now_ms=1000000)
    store.save_plan(plan)
    await submit_revision(store, scope, rev.id, request_id="req-late", now_ms=1000000)
    await approve_revision(store, principal, scope, revision_id=rev.id, plan_digest=plan.digest, now_ms=1000000)

    # Inbound message bumps contact context_version from 1 to 2
    store.record_ingress(scope, ContextEvent("e-late-2", "message", "customer", "Actually cancel that", None, "inbound"), source_id="src-late-2")

    # Claim part fails because context is stale
    claim = await claim_part(store, account, now_ms=1000050)
    assert claim is None

    # Late review or late approval cannot resurrect the stale revision
    with pytest.raises(BusinessConflict, match="stale_context"):
        await approve_revision(store, principal, scope, revision_id=rev.id, plan_digest=plan.digest, now_ms=1000060)

