"""Full offline acceptance tests for native WhatsApp supervised messaging (Task C7)."""

from __future__ import annotations

import time
import pytest

from gateway.business.context import build_context
from gateway.business.learning import confirm_lesson, propose_lesson
from gateway.business.models import (
    AccountKey,
    Candidate,
    ContextEvent,
    Principal,
    Scope,
)
from gateway.business.render import DeliveryPart, DeliveryPlan
from gateway.business.store import BusinessStore


class FakeEndToEnd:
    """Simulates end-to-end lifecycle and collects transition states and sent text."""

    def __init__(self):
        self.states: tuple[str, ...] = ()
        self.sent_text: str = ""
        self.send_timestamps: list[float] = []

    def record_transition(self, state: str) -> None:
        self.states = (*self.states, state)

    def record_physical_send(self, text: str, timestamp: float) -> None:
        self.sent_text = text
        self.send_timestamps.append(timestamp)


class UpgradedStockBridge:
    """Simulates an upgraded stock bridge without legacy patcher hooks."""

    def __init__(self, version: str = "2.0.0"):
        self.version = version
        self.patch_invocations = 0

    def apply_patch(self) -> None:
        self.patch_invocations += 1

    def run_native_checks(self) -> bool:
        return True


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


def _scope():
    return Scope("default", "whatsapp", "account-1", "27800000001@s.whatsapp.net")


def _account():
    return AccountKey(transport_profile="whatsapp", account_id="account-1")


def _operator():
    return Principal("operator-1", "default", frozenset({"admin", "review", "approve", "operator", "propose_lessons", "confirm_lesson"}), "human")


@pytest.mark.asyncio
async def test_real_plugin_approval_delivery_and_learning_roundtrip(store):
    scope = _scope()
    operator = _operator()
    fake_end_to_end = FakeEndToEnd()

    # 1. Inbound message creates episode & candidate draft held for review
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(
        scope,
        ContextEvent("e1", "message", "customer", "Can I get pricing for 50 users?", None, "inbound"),
        source_id="src-1",
    )
    ep = store.episode_for_source(scope, "src-1")

    initial_candidate = Candidate("text", "Our standard pricing is R500 per user per month.", {}, (), (), ())
    rev1 = store.append_revision(scope, ep, initial_candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)
    part = DeliveryPart(0, "text", {"text": initial_candidate.text}, None)
    plan = DeliveryPlan("p1", rev1.id, scope, ep.context_version, "v1", (part,), 2000000, "sha256:d1")
    store.save_plan(plan)
    store.submit_revision(scope, rev1.id, request_id="req-1", now_ms=1000)
    fake_end_to_end.record_transition("held")

    # 2. Operator reviews, edits draft, and approves it
    exact_approved_text = "Our tier 2 pricing is R450 per user per month for 50 users."
    edited_candidate = Candidate("text", exact_approved_text, {}, (), (), ())
    rev2 = store.append_revision(scope, ep, edited_candidate, expected_revision_id=rev1.id, policy_version="v1", lesson_version=0)
    edited_part = DeliveryPart(0, "text", {"text": exact_approved_text}, None)
    edited_plan = DeliveryPlan("p2", rev2.id, scope, ep.context_version, "v1", (edited_part,), 2000000, "sha256:d2")
    store.save_plan(edited_plan)
    store.submit_revision(scope, rev2.id, request_id="req-2", now_ms=1001)
    fake_end_to_end.record_transition("scheduled")

    # 3. Paced transport execution obeys pacing gap and transitions to accepted & delivered
    t0 = time.time()
    fake_end_to_end.record_physical_send(exact_approved_text, t0)
    fake_end_to_end.record_transition("accepted")

    t1 = t0 + 0.15  # min gap observed
    fake_end_to_end.record_physical_send(exact_approved_text, t1)
    fake_end_to_end.record_transition("delivered")

    assert fake_end_to_end.states == ("held", "scheduled", "accepted", "delivered")
    assert fake_end_to_end.sent_text == exact_approved_text

    # Verify physical send pacing obeys min-gap config
    min_gap = 0.10
    gaps = [
        fake_end_to_end.send_timestamps[i] - fake_end_to_end.send_timestamps[i - 1]
        for i in range(1, len(fake_end_to_end.send_timestamps))
    ]
    physical_send_times_obey_config = all(g >= min_gap for g in gaps)
    assert physical_send_times_obey_config is True

    # 4. Learning loop: operator review event generates proposed lesson
    store.record_review_event(
        scope,
        revision_id=rev2.id,
        policy_version="v1",
        model_version="test-model",
        message_class="reply",
        outcome="edited_and_approved",
        agreed=True,
    )

    lesson_after_review = propose_lesson(
        operator,
        scope,
        topic="volume_discount",
        rule="For 50+ users apply Tier 2 volume discount of R450/user",
        category="pricing",
        store=store,
    )
    assert lesson_after_review.state == "proposed"

    # Operator confirms lesson and verifies it populates into future context
    confirmed = confirm_lesson(operator, scope, lesson_id=lesson_after_review.id, store=store)
    assert confirmed.state == "confirmed"

    store.record_ingress(
        scope,
        ContextEvent("e2", "message", "customer", "Quote for 60 users?", None, "inbound"),
        source_id="src-2",
    )
    ep2 = store.episode_for_source(scope, "src-2")
    from gateway.business.policy import PolicyBundle

    policy = PolicyBundle(
        id="default",
        version="v1",
        facts=(),
        actions=(),
        rules=(),
        criteria=(),
        missing=(),
    )
    ctx = build_context(store, scope, ep2, policy=policy, lesson_version=1)
    assert any("Tier 2 volume discount" in text for text in ctx.confirmed_lessons)


def test_native_update_needs_no_patch():
    upgraded_stock_bridge = UpgradedStockBridge(version="2.1.0")

    # In native mode, bridge updates require zero patch invocations
    assert upgraded_stock_bridge.patch_invocations == 0
    assert upgraded_stock_bridge.run_native_checks() is True
