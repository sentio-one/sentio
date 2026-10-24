"""The dashboard socket derives its profile scope from a verified grant, never a constant (phase 2).

``scope_for_identity`` is what ``/api/ws`` calls: with no access policy the dashboard keeps its
host-wide scope; with one, a missing or empty grant reaches nothing (fail closed) and only an admin
or the trusted internal credential gets host scope and operator.
"""
import pytest

from gateway.access_policy import parse_access_policy
from hermes_cli.dashboard_auth.access import scope_for_identity
from hermes_cli.dashboard_auth.ws_tickets import INTERNAL_PROVIDER, INTERNAL_USER_ID, mint_ticket

ISSUER = "https://sso.corp.example"
POLICY = parse_access_policy({"admin_groups": ["hermes-admins"]})
OPEN = parse_access_policy(None)


def _identity(groups=(), provider="self-hosted", **extra):
    base = {"user_id": "u1", "provider": provider, "issuer": ISSUER}
    if groups:
        base["groups"] = list(groups)
    return {**base, **extra}


def test_no_policy_keeps_the_hostwide_dashboard_scope():
    identity, operator = scope_for_identity(_identity(), OPEN)
    assert identity["profile_scope"] == "host" and "allowed_profiles" not in identity and operator is True


def test_admin_group_gets_host_scope_and_operator():
    identity, operator = scope_for_identity(_identity(["hermes-admins"]), POLICY)
    assert identity["profile_scope"] == "host" and operator is True


def test_group_member_gets_an_allowlist_and_no_operator():
    identity, operator = scope_for_identity(_identity(["hermes-profile-alice"]), POLICY)
    assert identity["allowed_profiles"] == ["alice"] and "profile_scope" not in identity and operator is False


def test_user_without_any_grant_reaches_nothing():
    identity, operator = scope_for_identity(_identity(), POLICY)
    assert identity["allowed_profiles"] == [] and "profile_scope" not in identity and operator is False


def test_a_caller_supplied_scope_in_the_identity_is_never_trusted():
    forged = _identity(profile_scope="host", allowed_profiles=["everything"], capabilities=["session:operator"])
    identity, operator = scope_for_identity(forged, POLICY)
    assert identity["allowed_profiles"] == [] and "profile_scope" not in identity and operator is False
    assert "capabilities" not in identity


def test_internal_credential_is_trusted_with_host_scope():
    identity, operator = scope_for_identity(
        {"user_id": INTERNAL_USER_ID, "provider": INTERNAL_PROVIDER}, POLICY)
    assert identity["profile_scope"] == "host" and operator is True


def test_admin_group_from_an_untrusted_provider_is_not_admin():
    identity, operator = scope_for_identity(_identity(["hermes-admins"], provider="nous"), POLICY)
    assert identity["allowed_profiles"] == [] and operator is False


def test_groups_travel_through_a_ws_ticket_into_the_stamped_identity():
    from hermes_cli import web_server_chat as chat
    from tests.hermes_cli.test_dashboard_auth_ws_auth import _fake_ws

    ticket = mint_ticket(user_id="u1", provider="self-hosted", issuer=ISSUER,
                         extra={"groups": ["hermes-profile-alice"]})
    ws = _fake_ws(query={"ticket": ticket}, path="/api/ws")
    from hermes_cli import web_server
    web_server.app.state.auth_required = True
    try:
        assert chat._ws_auth_reason(ws)[0] is None
    finally:
        web_server.app.state.auth_required = False
    assert ws._hermes_auth_identity == {
        "user_id": "u1", "provider": "self-hosted", "issuer": ISSUER, "groups": ["hermes-profile-alice"]}


@pytest.mark.parametrize("groups", [[], None])
def test_identity_without_groups_is_unchanged_by_the_stamp(groups):
    from hermes_cli import web_server, web_server_chat as chat
    from tests.hermes_cli.test_dashboard_auth_ws_auth import _fake_ws

    ticket = mint_ticket(user_id="u1", provider="p", extra={"groups": groups})
    ws = _fake_ws(query={"ticket": ticket}, path="/api/ws")
    web_server.app.state.auth_required = True
    try:
        assert chat._ws_auth_reason(ws)[0] is None
    finally:
        web_server.app.state.auth_required = False
    assert ws._hermes_auth_identity == {"user_id": "u1", "provider": "p"}
