"""Per-user profile access policy: who may reach which served profile (private fork, phase 1).

The policy is fail-closed: an unknown caller, an unconfigured policy and a malformed grant all
resolve to "no profiles". Subjects are the dashboard's own ``authenticated_subject`` triple, so
two issuers (or two tenants behind one issuer host) never share a grant.
"""
import pytest

from gateway.access_policy import AccessPolicy, parse_access_policy
from gateway.config import GatewayConfig
from gateway.session_identity import authenticated_subject

ISSUER = "https://sso.corp.example"
RAW = {
    "admin_groups": ["hermes-admins"],
    "profile_group_prefix": "hermes-profile-",
    "users": [{"provider": "self_hosted", "issuer": ISSUER, "sub": "u-carol", "profiles": ["carol", "shared"]}],
    "admins": [{"provider": "self_hosted", "issuer": ISSUER, "sub": "u-root"}],
}


def _grant(policy, sub, groups=(), issuer=ISSUER, provider="self_hosted"):
    return policy.grant_for(provider=provider, issuer=issuer, sub=sub, groups=tuple(groups))


def test_unconfigured_policy_grants_nothing_even_to_admin_looking_groups():
    policy = parse_access_policy(None)
    assert policy.configured is False
    grant = _grant(policy, "u1", ["hermes-admins", "hermes-profile-alice"])
    assert grant.admin is False and grant.profiles == frozenset()


def test_group_prefix_grants_exactly_the_named_profiles():
    grant = _grant(parse_access_policy(RAW), "u-alice", ["hermes-profile-alice", "hermes-profile-shared", "other"])
    assert grant.profiles == {"alice", "shared"} and grant.admin is False
    assert grant.allows("alice") and not grant.allows("bob")


def test_unknown_user_without_groups_reaches_nothing():
    grant = _grant(parse_access_policy(RAW), "u-nobody")
    assert grant.profiles == frozenset() and not grant.allows("default")


def test_admin_group_and_admin_subject_reach_every_profile():
    policy = parse_access_policy(RAW)
    for grant in (_grant(policy, "u1", ["hermes-admins"]), _grant(policy, "u-root")):
        assert grant.admin is True
        assert grant.allows("anything") and grant.allows("default")


def test_static_user_grant_merges_with_group_grant():
    grant = _grant(parse_access_policy(RAW), "u-carol", ["hermes-profile-alice"])
    assert grant.profiles == {"alice", "carol", "shared"}


def test_same_sub_under_another_issuer_or_provider_is_a_different_subject():
    policy = parse_access_policy(RAW)
    assert _grant(policy, "u-root", issuer="https://other.example").admin is False
    assert _grant(policy, "u-root", provider="nous").admin is False


def test_two_tenants_behind_one_issuer_host_do_not_share_a_grant():
    entra = "https://login.example.net"
    raw = {"admins": [{"provider": "self_hosted", "issuer": f"{entra}/tenant-a/v2.0", "sub": "same-sub"}]}
    policy = parse_access_policy(raw)
    assert _grant(policy, "same-sub", issuer=f"{entra}/tenant-a/v2.0").admin is True
    assert _grant(policy, "same-sub", issuer=f"{entra}/tenant-b/v2.0").admin is False


def test_subject_key_is_the_one_the_session_authority_uses():
    policy = parse_access_policy(RAW)
    identity = {"user_id": "u-root", "provider": "self_hosted", "issuer": ISSUER}
    assert policy.subject_key("self_hosted", ISSUER, "u-root") == authenticated_subject(identity)


@pytest.mark.parametrize("bad", ["../etc", "a/b", "", "UPPER CASE", "x" * 200])
def test_invalid_profile_names_are_dropped_not_granted(bad):
    grant = _grant(parse_access_policy(RAW), "u1", [f"hermes-profile-{bad}"])
    assert grant.profiles == frozenset()
    raw = {"users": [{"provider": "p", "issuer": ISSUER, "sub": "u1", "profiles": [bad, "ok"]}]}
    assert _grant(parse_access_policy(raw), "u1", provider="p").profiles == {"ok"}


@pytest.mark.parametrize("raw", [
    "string", 7, [], {"users": "x"}, {"users": [None, 3, {"sub": "u"}]},
    {"admins": [{"provider": "p", "issuer": ISSUER}]}, {"admin_groups": "hermes-admins"},
    {"users": [{"provider": "p", "issuer": ISSUER, "sub": "u", "profiles": "alice"}]},
])
def test_malformed_policy_never_widens_access(raw):
    policy = parse_access_policy(raw)
    grant = _grant(policy, "u", ["hermes-admins"], provider="p")
    assert grant.admin is False and grant.profiles == frozenset()


def test_empty_prefix_is_refused_so_every_group_cannot_become_a_profile():
    policy = parse_access_policy({"profile_group_prefix": "", "admin_groups": ["a"]})
    assert _grant(policy, "u", ["alice"]).profiles == frozenset()


def test_gateway_config_reads_access_from_top_level_and_gateway_section():
    top = GatewayConfig.from_dict({"access": RAW}).access
    nested = GatewayConfig.from_dict({"gateway": {"access": RAW}}).access
    for policy in (top, nested):
        assert isinstance(policy, AccessPolicy) and policy.configured
        assert _grant(policy, "u1", ["hermes-admins"]).admin is True
    assert GatewayConfig.from_dict({}).access.configured is False


def test_access_survives_the_user_yaml_loader(tmp_path, monkeypatch):
    """The gateway reads raw YAML through the bridge, not DEFAULT_CONFIG: an `access:` block in
    config.yaml must reach GatewayConfig."""
    import json
    from gateway.config import load_gateway_config

    (tmp_path / "config.yaml").write_text(json.dumps({"access": RAW}))  # JSON is valid YAML
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    policy = load_gateway_config().access
    assert policy.configured and _grant(policy, "u-root").admin is True


def test_group_grants_count_only_for_trusted_group_providers():
    policy = parse_access_policy(RAW)
    assert _grant(policy, "u1", ["hermes-admins", "hermes-profile-alice"], provider="nous").profiles == frozenset()
    assert _grant(policy, "u1", ["hermes-admins"], provider="nous").admin is False
    wider = parse_access_policy({**RAW, "group_providers": ["nous"]})
    assert _grant(wider, "u1", ["hermes-admins"], provider="nous").admin is True
