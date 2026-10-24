"""The first admin of a company gateway can be named by verified email (private fork, FORK_DECISIONS D14/O13).

Google's ID token has no ``groups`` claim and its ``sub`` is unknown until a person has signed in once, so a company on Google
names its admin by email. The email counts only when the IdP marked it verified and the provider is one whose claims are trusted
for authorization, and a profile's own config can never add one (the managed file pins it).
"""
import json
from pathlib import Path

import pytest

from gateway.access_policy import DEFAULT_GROUP_PROVIDERS, parse_access_policy
from gateway.config import load_gateway_config
from hermes_cli.dashboard_auth.access import grant_for_identity, scope_for_identity
from plugins.dashboard_auth._shared import session_from_claims

ISSUER = "https://accounts.google.com"
POLICY = parse_access_policy({"admin_emails": ["Owner@Sentio.example", "second@sentio.example"]})
MANAGED_FILE = Path(__file__).resolve().parents[2] / "deploy" / "dokploy" / "managed" / "config.yaml"


def _grant(email, provider="self-hosted", policy=POLICY):
    return policy.grant_for(provider=provider, issuer=ISSUER, sub="1234", verified_email=email)


def test_a_verified_email_on_the_list_is_admin_whatever_its_case():
    assert _grant("owner@sentio.example").admin is True
    assert _grant("SECOND@Sentio.Example").admin is True


def test_an_email_off_the_list_or_empty_is_not_admin():
    assert _grant("someone@sentio.example").admin is False
    assert _grant("").admin is False


def test_an_untrusted_provider_cannot_mint_an_admin_by_email():
    assert _grant("owner@sentio.example", provider="nous").admin is False


def test_a_comma_separated_string_names_several_admins():
    policy = parse_access_policy({"admin_emails": [" a@x.example , b@x.example ,, "]})
    assert _grant("a@x.example", policy=policy).admin and _grant("b@x.example", policy=policy).admin


def test_an_unconfigured_policy_grants_nothing_to_a_listed_email():
    assert _grant("owner@sentio.example", policy=parse_access_policy(None)).admin is False


def test_the_session_carries_the_email_only_when_the_idp_verified_it():
    def session(**claims):
        return session_from_claims("self-hosted", {"sub": "u", "exp": 10, **claims}, access_token="t", refresh_token="r",
                                   email=claims.get("email", ""))

    assert session(email="Owner@Sentio.example", email_verified=True).verified_email == "owner@sentio.example"
    for unverified in ({"email": "owner@sentio.example"}, {"email": "owner@sentio.example", "email_verified": False},
                       {"email": "owner@sentio.example", "email_verified": "true"}):
        assert session(**unverified).verified_email == ""


def test_the_grant_follows_the_verified_email_on_every_path_a_request_can_take():
    kwargs = dict(provider="self-hosted", issuer=ISSUER, sub="1234")
    assert grant_for_identity(POLICY, verified_email="owner@sentio.example", **kwargs).admin is True
    identity = {"user_id": "1234", "provider": "self-hosted", "issuer": ISSUER, "verified_email": "owner@sentio.example"}
    scoped, operator = scope_for_identity(identity, POLICY)
    assert operator is True and scoped["profile_scope"] == "host"
    assert scope_for_identity({**identity, "verified_email": ""}, POLICY)[1] is False


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    from hermes_cli import managed_scope

    home, managed = tmp_path / "home", tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    (managed / "config.yaml").write_text(MANAGED_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    monkeypatch.setenv("HERMES_ADMIN_EMAILS", "owner@sentio.example,second@sentio.example")
    monkeypatch.delenv("HERMES_ADMIN_GROUP", raising=False)
    managed_scope.invalidate_managed_cache()
    yield home
    managed_scope.invalidate_managed_cache()


def test_the_trusted_provider_is_the_name_the_real_login_plugin_reports(deployment):
    """A login's session carries the plugin's own ``name``; a trusted-provider list spelled differently never matches it, so no
    admin (by group or by email) could ever sign in. Found on the first real deploy."""
    from plugins.dashboard_auth.self_hosted import SelfHostedOIDCProvider

    assert DEFAULT_GROUP_PROVIDERS == {SelfHostedOIDCProvider.name}
    assert load_gateway_config().access.group_providers == {SelfHostedOIDCProvider.name}


def test_the_deployment_names_its_admins_by_email_with_no_admin_group(deployment):
    policy = load_gateway_config().access
    assert _grant("second@sentio.example", policy=policy).admin is True
    assert _grant("someone@sentio.example", policy=policy).admin is False


def test_the_profiles_own_config_cannot_add_an_admin_email(deployment):
    (deployment / "config.yaml").write_text(json.dumps({"access": {"admin_emails": ["mallory@evil.example"]}}), encoding="utf-8")
    policy = load_gateway_config().access
    assert _grant("mallory@evil.example", policy=policy).admin is False
    assert _grant("owner@sentio.example", policy=policy).admin is True


def test_unset_admin_emails_fail_closed(deployment, monkeypatch):
    monkeypatch.delenv("HERMES_ADMIN_EMAILS")
    policy = load_gateway_config().access
    assert policy.configured is True and _grant("", policy=policy).admin is False
