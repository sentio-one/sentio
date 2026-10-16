"""Verified OIDC ``groups`` reach ``Session.groups`` (private fork, phase 1).

The groups claim is the authorization input for per-profile access, so it is parsed strictly:
only a list of non-empty strings counts, and a malformed claim yields no groups (never a crash
and never a widened grant).
"""
import pytest

from hermes_cli.dashboard_auth import Session
from plugins.dashboard_auth._shared import groups_from_claims, session_from_claims


def test_session_defaults_to_no_groups():
    session = Session(user_id="u", email="", display_name="", org_id="", provider="p", expires_at=1,
                      access_token="t", refresh_token="r")
    assert session.groups == ()


def test_groups_from_claims_accepts_a_list_of_strings_in_order_without_duplicates():
    assert groups_from_claims({"groups": [" a ", "b", "a", "", None, 3]}) == ("a", "b")


@pytest.mark.parametrize("claim", [None, "a,b", 5, {"a": 1}, [[1]]])
def test_groups_from_claims_ignores_a_malformed_claim(claim):
    assert groups_from_claims({"groups": claim}) == ()
    assert groups_from_claims({}) == ()


def test_groups_from_claims_is_bounded():
    assert len(groups_from_claims({"groups": [f"g{i}" for i in range(5000)]})) <= 512


def test_session_from_claims_carries_groups():
    session = session_from_claims("p", {"sub": "u", "exp": 10, "groups": ["hermes-admins"]},
                                  access_token="t", refresh_token="r")
    assert session.groups == ("hermes-admins",)


def test_self_hosted_session_maps_groups_and_keeps_org_id_behaviour():
    import plugins.dashboard_auth.self_hosted as oidc_plugin

    provider = oidc_plugin.SelfHostedOIDCProvider(issuer="https://auth.example.com", client_id="c")
    claims = {"sub": "u", "exp": 10, "iss": "https://auth.example.com", "groups": ["g1", "g2"]}
    session = provider._session("idt", "rt", claims)
    assert session.groups == ("g1", "g2")
    assert session.org_id == "g1,g2"  # unchanged legacy folding; authorization never reads it
