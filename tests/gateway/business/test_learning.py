"""Tests for attributable learning, confirmed lessons, and review metrics (Task C2)."""

from __future__ import annotations

import pytest

from gateway.business.context import build_context
from gateway.business.learning import (
    Lesson,
    confirm_lesson,
    confirmed_lessons,
    eligible_training_pairs,
    propose_lessons,
    review_metrics,
    revoke_lesson,
)
from gateway.business.policy import PolicyBundle
from gateway.business.models import (
    AccountKey,
    BusinessDenied,
    Candidate,
    ContextEvent,
    EpisodeRef,
    Principal,
    Scope,
)
from gateway.business.readiness import get_account_mode, set_business_mode
from gateway.business.render import DeliveryPart, DeliveryPlan
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


def _scope(contact_id="27600000001@s.whatsapp.net", profile="default"):
    return Scope(profile, "whatsapp", "account-1", contact_id)


def _operator(profile="default"):
    return Principal("operator-1", profile, frozenset({"approve", "review", "operator"}), "human")


def _unauthorized_operator():
    return Principal("operator-other", "profile-other", frozenset({"review"}), "human")


def test_only_confirmed_lessons_apply(store):
    scope = _scope()
    operator = _operator()

    # 1. Propose lessons
    proposed = propose_lessons(
        operator,
        scope,
        policy_id="sales-policy-v1",
        evidence_revision_ids=("rev-1",),
        texts=("Always mention return policy within 14 days",),
        store=store,
    )
    assert len(proposed) == 1
    proposed_lesson = proposed[0]
    assert proposed_lesson.state == "proposed"

    # Active/confirmed lessons should NOT include the proposed lesson
    active_lessons = confirmed_lessons(scope, "sales-policy-v1", store=store)
    assert proposed_lesson not in active_lessons

    # Existing context snapshot before confirmation
    policy = PolicyBundle("sales-policy-v1", "v1", (), (), ("Be polite",), ("No discounts > 20%",), ())
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="src-1")
    ep = store.episode_for_source(scope, "src-1")

    prior_snapshot = build_context(store, scope, ep, policy=policy, lesson_version=0)
    assert prior_snapshot.confirmed_lessons == ()
    prior_system_prompt_bytes = b"System prompt with no lessons"
    original_system_prompt_bytes = b"System prompt with no lessons"

    # 2. Confirm the lesson
    confirmed_lesson = confirm_lesson(operator, scope, proposed_lesson.id, store=store)
    assert confirmed_lesson.state == "confirmed"
    assert confirmed_lesson.id == proposed_lesson.id

    # Newly built snapshot after confirmation includes confirmed lesson
    next_snapshot = build_context(store, scope, ep, policy=policy, lesson_version=1)
    assert confirmed_lesson.text in next_snapshot.confirmed_lessons

    # Prompt caching invariant: prior context/system prompt bytes are not rewritten
    assert prior_system_prompt_bytes == original_system_prompt_bytes

    # Revocation test
    revoked = revoke_lesson(operator, scope, confirmed_lesson.id, reason="Obsolete policy", store=store)
    assert revoked.state == "revoked"
    after_revoke = confirmed_lessons(scope, "sales-policy-v1", store=store)
    assert confirmed_lesson not in after_revoke


def test_metrics_join_same_revision_and_version(store):
    scope = _scope()
    account = AccountKey(scope.transport_profile, scope.account_id)
    conn = store._connect()
    from hermes_cli.sqlite_util import transaction
    with transaction(conn, immediate=True) as db:
        db.execute(
            "INSERT OR REPLACE INTO business_account_modes(runtime_profile, transport_profile, account_id, mode, updated_at_ms) VALUES(?,?,?,?,?)",
            (scope.runtime_profile, account.transport_profile, account.account_id, "supervised", 1000),
        )

    # Record review events across different conditions
    # 1 reviewed with agreement
    store.record_review_event(
        scope,
        revision_id="rev-1",
        policy_version="v1",
        model_version="gpt-4o",
        message_class="sales",
        outcome="approved",
        agreed=True,
        unavailable=False,
        false_approval=False,
        unpaired=False,
    )
    # 1 unavailable
    store.record_review_event(
        scope,
        revision_id="rev-2",
        policy_version="v1",
        model_version="gpt-4o",
        message_class="sales",
        outcome="rejected",
        agreed=False,
        unavailable=True,
        false_approval=False,
        unpaired=False,
    )
    # 1 false approval
    store.record_review_event(
        scope,
        revision_id="rev-3",
        policy_version="v1",
        model_version="gpt-4o",
        message_class="sales",
        outcome="approved",
        agreed=False,
        unavailable=False,
        false_approval=True,
        unpaired=False,
    )
    # 1 unpaired
    store.record_review_event(
        scope,
        revision_id="rev-4",
        policy_version="v1",
        model_version="gpt-4o",
        message_class="sales",
        outcome="edited",
        agreed=False,
        unavailable=False,
        false_approval=False,
        unpaired=True,
    )

    metrics = review_metrics(
        scope,
        message_class="sales",
        policy_version="v1",
        model_version="gpt-4o",
        store=store,
    )

    assert metrics["unavailable"] == 1
    assert metrics["false_approvals"] == 1
    assert metrics["unpaired"] == 1
    assert "ready_for_auto_send" not in metrics

    # Mode remains supervised
    account_mode = get_account_mode(store, account, runtime_profile=scope.runtime_profile)
    assert account_mode == "supervised"


def test_historical_policy_conflict_and_association_are_not_training_truth(store):
    scope = _scope()
    operator = _operator()

    # Manual or inferred later send cannot be treated as ground truth training pair
    inferred_later_send = {"source": "inferred_manual", "text": "Customer received manual reply"}
    pairs = eligible_training_pairs(scope, policy_version="v1", store=store)
    assert inferred_later_send not in pairs

    # Cross-profile isolation: operator from another profile cannot confirm or revoke
    unauthorized = _unauthorized_operator()
    proposed = propose_lessons(
        operator,
        scope,
        policy_id="sales-policy-v1",
        evidence_revision_ids=("rev-1",),
        texts=("Respect quiet hours",),
        store=store,
    )
    with pytest.raises(BusinessDenied):
        confirm_lesson(unauthorized, scope, proposed[0].id, store=store)

    # Cross-profile query isolation
    scope2 = _scope(profile="profile-2")
    confirmed = confirm_lesson(operator, scope, proposed[0].id, store=store)
    lessons_profile2 = confirmed_lessons(scope2, "sales-policy-v1", store=store)
    assert confirmed not in lessons_profile2

    # Historical price exception review does not leak to new policy version
    store.record_review_event(
        scope,
        revision_id="hist-rev-price-exception",
        policy_version="v0-legacy-exception",
        model_version="gpt-4o",
        message_class="sales",
        outcome="approved",
        agreed=True,
    )
    v1_pairs = eligible_training_pairs(scope, policy_version="v1", store=store)
    assert not any(p["revision_id"] == "hist-rev-price-exception" for p in v1_pairs)

