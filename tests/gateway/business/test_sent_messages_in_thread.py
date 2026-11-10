"""What was actually sent joins the contact's thread, so drafting, review and cards see both sides."""
import pytest

from gateway.business.models import AccountKey, Candidate, ContextEvent, Principal, Scope
from gateway.business.render import DeliveryPart, DeliveryPlan
from gateway.business.store import BusinessStore

ACCOUNT = AccountKey(transport_profile="whatsapp", account_id="account-1")


def _sent_two_part_message(tmp_path):
    store = BusinessStore(tmp_path)
    scope = Scope("default", "whatsapp", "account-1", "27600000001@s.whatsapp.net")
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="src-1")
    episode = store.episode_for_source(scope, "src-1")
    revision = store.append_revision(scope, episode, Candidate("text", "Part one Part two", {}, (), (), ()),
                                     expected_revision_id=None, policy_version="v1", lesson_version=0)
    plan = DeliveryPlan("p1", revision.id, scope, episode.context_version, "v1",
                        (DeliveryPart(0, "text", {"text": "Part one"}, None), DeliveryPart(1, "text", {"text": "Part two"}, None)),
                        2_000_000, "sha256:d1")
    store.save_plan(plan)
    store.submit_revision(scope, revision.id, request_id="req-1", now_ms=1000)
    store.approve_revision(Principal("op", "default", frozenset({"approve"}), "human"), scope,
                           revision_id=revision.id, plan_digest=plan.digest, now_ms=1000)
    return store, scope, episode


def _thread(store, scope):
    return [(e.actor, e.text) for e in store.list_events(scope)]


def test_each_sent_part_joins_the_thread_once_and_in_order(tmp_path):
    store, scope, _ = _sent_two_part_message(tmp_path)
    first = store.claim_part(ACCOUNT, now_ms=1000)
    store.update_attempt_state(first.id, state="sent", platform_ids=("m1",))
    store.update_attempt_state(first.id, state="delivered")
    store.update_attempt_state(first.id, state="read")
    assert _thread(store, scope) == [("customer", "Hi"), ("agent", "Part one")]
    second = store.claim_part(ACCOUNT, now_ms=1000)
    store.update_attempt_state(second.id, state="sent", platform_ids=("m2",))
    assert _thread(store, scope) == [("customer", "Hi"), ("agent", "Part one"), ("agent", "Part two")]


def test_a_message_that_was_not_sent_never_appears(tmp_path):
    store, scope, _ = _sent_two_part_message(tmp_path)
    attempt = store.claim_part(ACCOUNT, now_ms=1000)
    store.update_attempt_state(attempt.id, state="failed", error_code="bridge_down")
    store.update_attempt_state(attempt.id, state="scheduled", error_code="outside_window")
    assert _thread(store, scope) == [("customer", "Hi")]


def test_recording_a_sent_message_does_not_make_pending_drafts_stale(tmp_path):
    store, scope, episode = _sent_two_part_message(tmp_path)
    before = store.episode_for_source(scope, "src-1").context_version
    attempt = store.claim_part(ACCOUNT, now_ms=1000)
    store.update_attempt_state(attempt.id, state="sent", platform_ids=("m1",))
    assert store.episode_for_source(scope, "src-1").context_version == before == episode.context_version
