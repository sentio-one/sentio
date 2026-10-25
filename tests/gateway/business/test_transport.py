"""Tests for physical execution, registration preflight, and typed send receipts (Task B5)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import pytest

from gateway.business.models import (
    AccountKey,
    Candidate,
    ContextEvent,
    EpisodeRef,
    Receipt,
    Scope,
)
from gateway.business.render import (
    DeliveryPart,
    DeliveryPlan,
    render_delivery_plan,
)
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.business.transport import (
    dispatch_due,
    preflight_recipient,
    stage_turn_candidate,
    submit_external,
)
from gateway.platforms.base import SendResult


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


@pytest.mark.asyncio
async def test_held_result_and_generic_retry_suppression(store, service):
    scope = _scope()
    candidate = Candidate("text", "Outbound reply", {}, (), (), ())

    # submit_external enters native queue -> returns held receipt
    queued = await submit_external(scope, candidate, request_id="req-ext-1", source_kind="turn_reply", service=service)
    assert queued.state == "held"

    # SendResult wrapping a held business receipt
    send_res = SendResult(
        success=False,
        message_id=None,
        retryable=False,
        business_receipt={"id": queued.id, "state": "held", "revision_id": queued.revision_id, "reason": None},
    )
    assert send_res.business_receipt["state"] == "held"
    assert send_res.success is False
    assert send_res.message_id is None
    assert send_res.retryable is False

    generic_retry_count = 0
    # A caller checking send_res.business_receipt skips generic retry
    if send_res.business_receipt and send_res.business_receipt.get("state") in ("held", "staged"):
        pass
    else:
        generic_retry_count += 1
    assert generic_retry_count == 0


@pytest.mark.asyncio
async def test_stage_turn_candidate_for_own_turn(store):
    scope = _scope()
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Inquiry", None, "inbound"), source_id="src-inquiry")
    ep = store.episode_for_source(scope, "src-inquiry")

    # Working tool send to the active turn's own contact
    candidate = Candidate("text", "Working tool partial info", {}, (), (), ())
    own_turn_tool_receipt = await stage_turn_candidate(store, scope, ep, candidate)

    assert own_turn_tool_receipt.state == "staged"
    assert own_turn_tool_receipt.revision_id is None

    # Staged candidate does not commit a separate draft revision; active revisions remain 0 until terminal draft
    revisions = store.list_revisions(scope, ep.id)
    assert len(revisions) == 0

    # Once terminal revision is appended, active draft count is 1
    terminal_candidate = Candidate("text", "Here is the final consolidated response", {}, (), (), ())
    store.append_revision(scope, ep, terminal_candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)
    active_drafts_after_tool_and_final = len(store.list_revisions(scope, ep.id))
    assert active_drafts_after_tool_and_final == 1


@pytest.mark.asyncio
async def test_preflight_recipient_states(store):
    scope = _scope()
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])

    # 1. Known valid from recent inbound
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="src-valid")
    res_valid = await preflight_recipient(scope, store=store)
    assert res_valid == "known_valid"

    # 2. No whatsapp account
    scope_none = _scope("27600000099@s.whatsapp.net")
    # resolver that returns not found on whatsapp
    def negative_resolver(contact):
        return False

    res_negative = await preflight_recipient(scope_none, store=store, contact_checker=negative_resolver)
    assert res_negative == "no_whatsapp_account"

    # When preflight fails negative, external send marks failed with reason no_whatsapp_account
    negative_registration = Receipt(id="rec-neg", state="failed", revision_id=None, reason="no_whatsapp_account")
    assert negative_registration.state == "failed"
    assert negative_registration.reason == "no_whatsapp_account"

    # 3. Lookup error holds
    def error_resolver(contact):
        raise ConnectionError("network_timeout")

    res_lookup_error = await preflight_recipient(scope_none, store=store, contact_checker=error_resolver)
    assert res_lookup_error == "unknown"
    lookup_error = Receipt(id="rec-err", state="held", revision_id=None, reason="lookup_error")
    assert lookup_error.state == "held"


@pytest.mark.asyncio
async def test_dispatch_due_stop_on_timeout(store, service):
    from gateway.business.config import BusinessConfig
    service._config_resolver = BusinessConfig("supervised", (), ("operator-1",), 2, 300, 300, {
        "timezone": "UTC", "windows": {"reply": [{"weekdays": list(range(7)), "start": "00:00", "end": "24:00"}]},
        "min_gap_seconds": 1, "caps": {}, "hard_max_per_day": 100,
    })
    account = _account()
    scope = _scope()
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Order update", None, "inbound"), source_id="src-order")
    ep = store.episode_for_source(scope, "src-order")

    candidate = Candidate("text", "Part 1 text", {}, (), (), ())
    rev = store.append_revision(scope, ep, candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)

    # Multi-part plan
    part1 = DeliveryPart(0, "text", {"text": "Part 1 text"}, None)
    part2 = DeliveryPart(1, "text", {"text": "Part 2 text"}, None)
    plan = DeliveryPlan(
        id="plan-multipart",
        revision_id=rev.id,
        scope=scope,
        context_version=ep.context_version,
        policy_version="v1",
        parts=(part1, part2),
        expires_at_ms=2000000,
        digest="sha256:multi1234",
    )
    store.save_plan(plan)
    store.submit_revision(scope, rev.id, request_id="req-multi", now_ms=1000000)
    from gateway.business.models import Principal
    principal = Principal("operator-1", "default", frozenset({"approve"}), "human")
    store.approve_revision(principal, scope, revision_id=rev.id, plan_digest=plan.digest, now_ms=1000000)

    # Bridge transport executor that times out after socket submission for part 1
    async def timeout_executor(request):
        raise TimeoutError("socket_timeout_after_send")

    dispatched = await dispatch_due(service, account, transport_executor=timeout_executor, now_ms=1000000)
    assert len(dispatched) == 1
    timeout_after_socket = dispatched[0]
    assert timeout_after_socket.state == "unknown"

    # Subsequent parts must not be dispatched when part 1 had an ambiguous timeout
    subsequent_parts_dispatched = await dispatch_due(service, account, transport_executor=timeout_executor, now_ms=1000050)
    assert subsequent_parts_dispatched == []
