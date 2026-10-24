import pytest

import sqlite3

from gateway.business.models import Candidate, ContextEvent, ContextSnapshot, EpisodeRef, Scope
from gateway.business.models import BusinessConflict, BusinessDenied
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


def _scope(contact="contact-1", profile="runtime-a"):
    return Scope(profile, "whatsapp", "account-1", contact)


def _event(text="Need a quote"):
    return ContextEvent("event-1", "message", "customer", text, None, "inbound")


def _candidate(text="I can help with that"):
    return Candidate("text", text, {}, (), (), ())


def test_ingress_idempotency_and_profile_isolation(store):
    scope = _scope()
    first = store.record_ingress(scope, _event(), source_id="inbound-1")
    assert store.record_ingress(scope, _event(), source_id="inbound-1") == first
    with pytest.raises(BusinessConflict):
        store.record_ingress(scope, _event("Changed"), source_id="inbound-1")
    assert store.list_events(scope) == (_event(),)
    snapshot = _snapshot(scope, first)
    store.save_snapshot(snapshot)
    assert store.get_snapshot(scope, snapshot.id) == snapshot
    with pytest.raises(BusinessDenied):
        store.save_snapshot(_snapshot(scope, EpisodeRef(first.id, first.context_version + 1)))
    foreign = _scope(profile="runtime-b")
    with pytest.raises(BusinessDenied):
        store.get_snapshot(foreign, snapshot.id)
    store.record_ingress(scope, ContextEvent("event-2", "message", "customer", "More", None, "inbound"), source_id="inbound-2")
    assert store.get_snapshot(scope, snapshot.id) == snapshot


def test_revision_compare_and_swap(store):
    scope = _scope()
    episode = store.record_ingress(scope, _event(), source_id="inbound-1")
    assert store.episode_status(scope, episode.id) == "open"
    candidate = Candidate("text", "Draft", {"meta": {"label": "original"}}, ("fact-1",), (), ())
    revision = store.append_revision(scope, episode, candidate, expected_revision_id=None,
                                     policy_version="p1", lesson_version=0)
    candidate.data["meta"]["label"] = "mutated after write"
    with pytest.raises(BusinessDenied):
        store.get_revision(_scope(profile="runtime-b"), revision.id)
    with pytest.raises(BusinessConflict):
        store.append_revision(scope, episode, _candidate("Again"), expected_revision_id="stale",
                              policy_version="p1", lesson_version=0)
    assert store.get_revision(scope, revision.id) == revision
    assert store.get_revision(scope, revision.id).candidate.data["meta"]["label"] == "original"
    newer = store.record_ingress(scope, ContextEvent("event-2", "message", "customer", "More", None, "inbound"), source_id="inbound-2")
    with pytest.raises(BusinessConflict):
        store.append_revision(scope, episode, _candidate("Stale"), expected_revision_id=revision.id,
                              policy_version="p1", lesson_version=0)
    assert store.revision_status(scope, revision.id) == "superseded"
    with pytest.raises(BusinessConflict):
        store.append_revision(scope, EpisodeRef(episode.id, newer.context_version - 1), _candidate(),
                              expected_revision_id=revision.id, policy_version="p1", lesson_version=0)


def test_suppression_survives_alias_binding_and_restart(tmp_path):
    store = BusinessStore(tmp_path)
    scope = store.bind_contact("runtime-a", "whatsapp", "account-1", ("15551234@s.whatsapp.net",))
    store.set_suppressed(scope, reason="operator", source_id="operator-1")
    reopened = BusinessStore(tmp_path)
    assert reopened.is_suppressed(scope) is True
    alias_scope = reopened.bind_contact("runtime-a", "whatsapp", "account-1", ("15551234",))
    assert alias_scope == scope


def test_alias_collision_holds_existing_owners_without_creating_contact(tmp_path):
    store = BusinessStore(tmp_path)
    opted_out = store.bind_contact("runtime-a", "whatsapp", "account-1", ("111@s.whatsapp.net",))
    other = store.bind_contact("runtime-a", "whatsapp", "account-1", ("222@lid",))
    store.set_suppressed(opted_out, reason="stop", source_id="optout-1")
    with pytest.raises(BusinessConflict):
        store.bind_contact("runtime-a", "whatsapp", "account-1", ("111@s.whatsapp.net", "222@lid"))
    reopened = BusinessStore(tmp_path)
    assert reopened.is_held(opted_out) and reopened.is_held(other)
    assert reopened.is_suppressed(opted_out)
    assert reopened.bind_contact("runtime-a", "whatsapp", "account-1", ("111@s.whatsapp.net",)) == opted_out
    assert reopened.bind_contact("runtime-a", "whatsapp", "account-1", ("222@lid",)) == other
    assert set(reopened.contact_identifiers(opted_out)) == {"111@s.whatsapp.net"}
    assert set(reopened.contact_identifiers(other)) == {"222@lid"}
    assert len(reopened.list_tasks(opted_out)) == 1
    assert len(reopened.list_tasks(other)) == 1


def test_verdict_and_suppression_are_audited_and_idempotent(store):
    scope = _scope()
    episode = store.record_ingress(scope, _event(), source_id="inbound-1")
    first = store.record_verdict(scope, episode.id, kind="no_reply", reason="resolved")
    assert store.record_verdict(scope, episode.id, kind="no_reply", reason="resolved") == first
    store.set_suppressed(scope, reason="operator request", source_id="operator-stop-1")
    store.set_suppressed(scope, reason="operator request", source_id="operator-stop-1")
    assert store.is_suppressed(scope)
    assert store.episode_status(scope, episode.id) == "resolved"
    assert store.list_events(scope) == (_event(),)
    assert store.audit_events(scope)
    assert len(store.audit_events(scope)) == 2
    assert {item["reason"] for item in store.audit_events(scope)} == {"resolved", "operator request"}


def test_failed_commit_returns_no_revision_or_task_receipt(tmp_path, monkeypatch):
    store = BusinessStore(tmp_path)
    scope = _scope()
    episode = store.record_ingress(scope, _event(), source_id="inbound-1")
    connect = store._connect

    def refuse_commit():
        conn = connect()
        conn.set_authorizer(lambda action, arg1, *_: sqlite3.SQLITE_DENY
                            if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT"
                            else sqlite3.SQLITE_OK)
        return conn

    monkeypatch.setattr(store, "_connect", refuse_commit)
    with pytest.raises(sqlite3.DatabaseError):
        store.append_revision(scope, episode, _candidate(), expected_revision_id=None,
                             policy_version="p1", lesson_version=0)
    with pytest.raises(sqlite3.DatabaseError):
        store.put_task(scope, dedupe_key="task-1", reason="review", refs=(episode.id,))
    reopened = BusinessStore(tmp_path)
    with sqlite3.connect(tmp_path / "state.db") as db:
        revision_count = db.execute(
            "SELECT COUNT(*) FROM business_revisions WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
            (scope.runtime_profile, scope.transport_profile, scope.account_id, scope.contact_id),
        ).fetchone()[0]
    assert revision_count == 0
    assert reopened.list_tasks(scope) == ()


def _snapshot(scope, episode):
    return ContextSnapshot("snapshot-1", scope, episode, "p1", 0, (_event(),), (), (), (), (), (), ())

