import pytest

from gateway.business.models import Candidate, ContextEvent, Scope
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
    foreign = _scope(profile="runtime-b")
    with pytest.raises(BusinessDenied):
        store.get_snapshot(foreign, first.id)


def test_revision_compare_and_swap(store):
    scope = _scope()
    episode = store.record_ingress(scope, _event(), source_id="inbound-1")
    revision = store.append_revision(scope, episode, _candidate(), expected_revision_id=None,
                                     policy_version="p1", lesson_version=0)
    with pytest.raises(BusinessDenied):
        store.get_revision(_scope(profile="runtime-b"), revision.id)
    with pytest.raises(BusinessConflict):
        store.append_revision(scope, episode, _candidate("Again"), expected_revision_id="stale",
                              policy_version="p1", lesson_version=0)


def test_suppression_survives_alias_binding_and_restart(tmp_path):
    store = BusinessStore(tmp_path)
    scope = store.bind_contact("runtime-a", "whatsapp", "account-1", ("15551234@s.whatsapp.net",))
    store.set_suppressed(scope, reason="operator", source_id="operator-1")
    reopened = BusinessStore(tmp_path)
    assert reopened.is_suppressed(scope) is True
    alias_scope = reopened.bind_contact("runtime-a", "whatsapp", "account-1", ("15551234",))
    assert alias_scope == scope

