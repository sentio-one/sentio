"""Invite and grant store behind per-user profile access (private fork).

An invite is a server-side record (scope, expiry, use limit); the token only names it. Every refusal is
the same ``InviteError`` so a caller learns nothing about which invites exist, and redemption is atomic.
"""
import sqlite3
import threading

import pytest

from gateway.access_grants import GrantStore, InviteError

ADMIN = 'auth:v1:["self-hosted","https://sso.corp.example","u-admin"]'
ALICE = 'auth:v1:["self-hosted","https://sso.corp.example","u-alice"]'
BOB = 'auth:v1:["self-hosted","https://sso.corp.example","u-bob"]'
CAROL = 'auth:v1:["self-hosted","https://sso.corp.example","u-carol"]'


class _Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def store(tmp_path, clock):
    return GrantStore(tmp_path / "access_grants.db", clock=clock)


def _invite(store, **kwargs):
    return store.create_invite(profile=kwargs.pop("profile", "alpha"), created_by=ADMIN, **kwargs)


def test_redeeming_an_invite_grants_exactly_its_profile(store):
    created = _invite(store)
    assert store.redeem(created.token, subject=ALICE) == "alpha"
    assert store.profiles_for(ALICE) == {"alpha"}
    assert store.profiles_for(BOB) == frozenset()


def test_the_token_is_never_stored_or_listed(store, tmp_path):
    created = _invite(store)
    store.redeem(created.token, subject=ALICE)
    assert created.token.encode() not in (tmp_path / "access_grants.db").read_bytes()
    listing = str(store.list_invites()) + str(store.list_grants())
    assert created.token not in listing and "token_hash" not in listing


def test_a_single_use_invite_is_spent_by_its_first_redeemer(store):
    created = _invite(store)
    store.redeem(created.token, subject=ALICE)
    with pytest.raises(InviteError):
        store.redeem(created.token, subject=BOB)
    assert store.profiles_for(BOB) == frozenset()


def test_the_same_person_clicking_twice_is_idempotent_and_spends_one_use(store):
    created = _invite(store, max_uses=2)
    assert store.redeem(created.token, subject=ALICE) == "alpha"
    assert store.redeem(created.token, subject=ALICE) == "alpha"
    assert store.list_invites()[0]["uses"] == 1
    assert store.redeem(created.token, subject=BOB) == "alpha"
    with pytest.raises(InviteError):
        store.redeem(created.token, subject=CAROL)


def test_a_team_link_admits_max_uses_distinct_people(store):
    created = _invite(store, max_uses=3)
    for subject in (ALICE, BOB, CAROL):
        assert store.redeem(created.token, subject=subject) == "alpha"
    with pytest.raises(InviteError):
        store.redeem(created.token, subject='auth:v1:["self-hosted","https://sso.corp.example","u-dave"]')


def test_an_invite_expires(store, clock):
    created = _invite(store, ttl_hours=1)
    clock.now += 3599
    assert store.redeem(created.token, subject=ALICE) == "alpha"
    other = _invite(store, ttl_hours=1)
    clock.now += 3601
    with pytest.raises(InviteError):
        store.redeem(other.token, subject=BOB)


def test_a_revoked_invite_cannot_be_redeemed_but_existing_grants_survive_by_default(store):
    created = _invite(store, max_uses=5)
    store.redeem(created.token, subject=ALICE)
    assert store.revoke_invite(created.invite_id) is True
    with pytest.raises(InviteError):
        store.redeem(created.token, subject=BOB)
    assert store.profiles_for(ALICE) == {"alpha"}


def test_revoking_with_grants_removes_only_the_access_that_invite_created(store):
    first, second = _invite(store, max_uses=5), _invite(store, profile="beta", max_uses=5)
    store.redeem(first.token, subject=ALICE)
    store.redeem(second.token, subject=ALICE)
    store.revoke_invite(first.invite_id, revoke_grants=True)
    assert store.profiles_for(ALICE) == {"beta"}


def test_a_person_whose_grant_was_revoked_cannot_walk_back_in_with_the_same_link(store):
    created = _invite(store, max_uses=5)
    store.redeem(created.token, subject=ALICE)
    assert store.revoke_grant(ALICE, "alpha") is True
    assert store.profiles_for(ALICE) == frozenset()
    with pytest.raises(InviteError):
        store.redeem(created.token, subject=ALICE)
    fresh = _invite(store)
    assert store.redeem(fresh.token, subject=ALICE) == "alpha"  # a new, deliberate invite restores it


def test_an_email_bound_invite_needs_the_matching_verified_email(store):
    created = _invite(store, email=" Alice@Corp.Example ")
    for email in (None, "", "bob@corp.example"):
        with pytest.raises(InviteError):
            store.redeem(created.token, subject=BOB, email=email)
    assert store.redeem(created.token, subject=ALICE, email="ALICE@corp.example") == "alpha"


def test_every_refusal_is_the_same_error(store, clock):
    spent = _invite(store)
    store.redeem(spent.token, subject=ALICE)
    expired = _invite(store, ttl_hours=1)
    clock.now += 7200
    revoked = _invite(store)
    store.revoke_invite(revoked.invite_id)
    bound = _invite(store, email="a@corp.example")
    messages = set()
    for token, kwargs in ((spent.token, {}), (expired.token, {}), (revoked.token, {}), ("nope", {}), ("", {}),
                          (bound.token, {"email": "x@corp.example"}), ("x" * 5000, {})):
        with pytest.raises(InviteError) as raised:
            store.redeem(token, subject=BOB, **kwargs)
        messages.add((str(raised.value), raised.value.reason))
    assert messages == {("invalid_invite", "invalid_invite")}


def test_redemption_is_atomic_under_concurrency(store):
    created = _invite(store)
    outcomes, barrier = [], threading.Barrier(8)

    def attempt(n):
        barrier.wait()
        try:
            store.redeem(created.token, subject=f'auth:v1:["p","i","u{n}"]')
            outcomes.append("ok")
        except InviteError:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["ok"] + ["refused"] * 7
    assert store.list_invites()[0]["uses"] == 1


def test_state_survives_reopening_the_store(tmp_path, clock):
    path = tmp_path / "access_grants.db"
    first = GrantStore(path, clock=clock)
    created = _invite(first)
    first.redeem(created.token, subject=ALICE)
    assert GrantStore(path, clock=clock).profiles_for(ALICE) == {"alpha"}


def test_invites_report_their_status(store, clock):
    active, used, revoked, expired = _invite(store), _invite(store), _invite(store), _invite(store, ttl_hours=1)
    store.redeem(used.token, subject=ALICE)
    store.revoke_invite(revoked.invite_id)
    clock.now += 7200
    status = {row["id"]: row["status"] for row in store.list_invites()}
    assert status == {active.invite_id: "active", used.invite_id: "used_up",
                      revoked.invite_id: "revoked", expired.invite_id: "expired"}


@pytest.mark.parametrize("kwargs", [
    {"profile": "../etc"}, {"profile": ""}, {"profile": "A B"}, {"ttl_hours": 0}, {"ttl_hours": 24 * 31},
    {"max_uses": 0}, {"max_uses": 1001}, {"max_uses": True}, {"email": "no-at-sign"},
])
def test_creating_an_invite_validates_its_inputs(store, kwargs):
    with pytest.raises(ValueError):
        _invite(store, **kwargs)


def test_defaults_are_single_use_for_seven_days(store, clock):
    created = _invite(store)
    row = store.list_invites()[0]
    assert (row["max_uses"], row["expires_at"] - row["created_at"]) == (1, 7 * 24 * 3600)
    assert created.expires_at == row["expires_at"]


def test_grants_can_be_listed_and_revoked(store):
    store.redeem(_invite(store).token, subject=ALICE)
    assert [(g["subject"], g["profile"]) for g in store.list_grants()] == [(ALICE, "alpha")]
    assert store.revoke_grant(ALICE, "alpha") is True
    assert store.revoke_grant(ALICE, "alpha") is False
    assert store.list_grants() == []


def test_a_corrupt_database_is_refused_not_silently_replaced(tmp_path, clock):
    path = tmp_path / "access_grants.db"
    path.write_bytes(b"this is not a sqlite database" * 20)
    with pytest.raises(sqlite3.DatabaseError):
        GrantStore(path, clock=clock).profiles_for(ALICE)
