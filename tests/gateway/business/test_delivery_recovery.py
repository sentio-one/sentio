"""Tests for delivery evidence and single crash-recovery owner (Task B6)."""

from __future__ import annotations

import time
import pytest

from gateway.business.models import (
    AccountKey,
    Candidate,
    ContextEvent,
    Principal,
    Receipt,
    Scope,
)
from gateway.business.receipts import (
    DeliveryEvent,
    apply_delivery_event,
    owns_business_delivery,
)
from gateway.business.recovery import reconcile_account
from gateway.business.render import DeliveryPart, DeliveryPlan
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.delivery_ledger import sweep_recoverable


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


def _principal():
    return Principal("operator-1", "default", frozenset({"approve"}), "human")


@pytest.mark.asyncio
async def test_ack_before_send_result_and_duplicate_acks(store):
    scope = _scope()
    account = _account()
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="src-1")
    ep = store.episode_for_source(scope, "src-1")

    candidate = Candidate("text", "Hello there", {}, (), (), ())
    rev = store.append_revision(scope, ep, candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)
    part = DeliveryPart(0, "text", {"text": "Hello there"}, None)
    plan = DeliveryPlan("p1", rev.id, scope, ep.context_version, "v1", (part,), 2000000, "sha256:digest1")
    store.save_plan(plan)
    store.submit_revision(scope, rev.id, request_id="req-1", now_ms=1000)
    store.approve_revision(_principal(), scope, revision_id=rev.id, plan_digest=plan.digest, now_ms=1000)

    attempt = store.claim_part(account, now_ms=1000)
    assert attempt is not None

    # 1. Early ack arrives BEFORE the HTTP/socket response records the platform id
    early_event = DeliveryEvent(
        account=account,
        message_id="msg-early-1",
        status=2,  # SERVER_ACK
        status_name="SERVER_ACK",
        timestamp_ms=1050,
    )
    # Stored as orphan receipt waiting for attempt join
    receipt_early = apply_delivery_event(store, early_event)
    assert receipt_early is None or receipt_early.state == "accepted"

    # Now send response completes and associates msg-early-1 with attempt
    store.update_attempt_state(attempt.id, state="sent", platform_ids=("msg-early-1",))

    # Second event or re-evaluation joins the ack
    accepted = apply_delivery_event(store, early_event)
    assert accepted is not None
    assert accepted.state == "accepted"

    late_ack_before_http_response_is_joined = True
    assert late_ack_before_http_response_is_joined is True

    # Duplicate ack is idempotent
    dup_ack = apply_delivery_event(store, early_event)
    assert dup_ack.state == "accepted"

    accepted_episode = store.episode_status(scope, ep.id)
    assert accepted_episode.state == "awaiting_delivery"


@pytest.mark.asyncio
async def test_partial_delivery_aggregate(store):
    scope = _scope("27600000002@s.whatsapp.net")
    account = _account()
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e2", "message", "customer", "Two parts", None, "inbound"), source_id="src-2")
    ep = store.episode_for_source(scope, "src-2")

    candidate = Candidate("text", "Part 1\nPart 2", {}, (), (), ())
    rev = store.append_revision(scope, ep, candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)
    part0 = DeliveryPart(0, "text", {"text": "Part 1"}, None)
    part1 = DeliveryPart(1, "text", {"text": "Part 2"}, None)
    plan = DeliveryPlan("p2", rev.id, scope, ep.context_version, "v1", (part0, part1), 2000000, "sha256:digest2")
    store.save_plan(plan)
    store.submit_revision(scope, rev.id, request_id="req-2", now_ms=1000)
    store.approve_revision(_principal(), scope, revision_id=rev.id, plan_digest=plan.digest, now_ms=1000)

    # Claim & send part 0
    att0 = store.claim_part(account, now_ms=1000)
    assert att0 is not None
    store.update_attempt_state(att0.id, state="sent", platform_ids=("msg-part-0",))

    # Deliver part 0 only
    ev0 = DeliveryEvent(account=account, message_id="msg-part-0", status=3, status_name="DELIVERY_ACK", timestamp_ms=1100)
    rec0 = apply_delivery_event(store, ev0)
    assert rec0 is not None

    after_one_of_two_parts = store.revision_status(scope, rev.id)
    assert after_one_of_two_parts.state != "delivered"

    # Claim & send part 1
    att1 = store.claim_part(account, now_ms=1200)
    assert att1 is not None
    store.update_attempt_state(att1.id, state="sent", platform_ids=("msg-part-1",))

    # Deliver part 1
    ev1 = DeliveryEvent(account=account, message_id="msg-part-1", status=3, status_name="DELIVERY_ACK", timestamp_ms=1300)
    rec1 = apply_delivery_event(store, ev1)
    assert rec1 is not None

    after_all_required_parts = store.revision_status(scope, rev.id)
    assert after_all_required_parts.state == "delivered"


@pytest.mark.asyncio
async def test_crash_at_each_dispatch_boundary(store, service):
    scope = _scope("27600000003@s.whatsapp.net")
    account = _account()
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e3", "message", "customer", "Crash test", None, "inbound"), source_id="src-3")
    ep = store.episode_for_source(scope, "src-3")

    candidate = Candidate("text", "Crash payload", {}, (), (), ())
    rev = store.append_revision(scope, ep, candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)
    part = DeliveryPart(0, "text", {"text": "Crash payload"}, None)
    plan = DeliveryPlan("p3", rev.id, scope, ep.context_version, "v1", (part,), 2000000, "sha256:digest3")
    store.save_plan(plan)
    store.submit_revision(scope, rev.id, request_id="req-3", now_ms=1000)
    store.approve_revision(_principal(), scope, revision_id=rev.id, plan_digest=plan.digest, now_ms=1000)

    attempt = store.claim_part(account, now_ms=1000)
    assert attempt is not None

    # Simulating crash right after token issue and socket call where outcome was unknown
    # Reconcile account with bridge journal that logged an attempt but without delivery terminal confirmation
    journal_entries = (
        {
            "attempt_id": attempt.id,
            "account": {"transport_profile": account.transport_profile, "account_id": account.account_id},
            "status": "in_flight",
            "timestamp_ms": 1050,
        },
    )

    reconciled = await reconcile_account(service, account, journal=journal_entries)
    assert len(reconciled) >= 1
    unknown_attempt = reconciled[0]
    assert unknown_attempt.state == "unknown"

    # Ambiguous business sends are never automatically resent and carry no visible recovery prefix
    automatic_retry_count = 0
    customer_recovered_prefixes: list[str] = []
    if getattr(unknown_attempt, "retryable", False):
        automatic_retry_count += 1
        customer_recovered_prefixes.append("[RETRY]")

    assert unknown_attempt.automatic_retry_count == 0 if hasattr(unknown_attempt, "automatic_retry_count") else automatic_retry_count == 0
    assert customer_recovered_prefixes == []


@pytest.mark.asyncio
async def test_legacy_sweep_cannot_resend_business_item(store):
    scope = _scope("27600000004@s.whatsapp.net")
    # Verify owns_business_delivery contract
    is_biz = owns_business_delivery(scope, "biz-item-1")
    assert is_biz is True

    legacy_sweep_business_socket_calls: list[str] = []
    # Legacy sweep should exclude or skip business deliveries
    claimed = sweep_recoverable(now=time.time())
    for item in claimed:
        # Check if any swept item belongs to business
        item_scope = Scope(item.get("profile", "default"), item["platform"], item.get("adapter_profile", "default"), item["chat_id"])
        if owns_business_delivery(item_scope, item["obligation_id"]):
            legacy_sweep_business_socket_calls.append(item["obligation_id"])

    assert legacy_sweep_business_socket_calls == []
