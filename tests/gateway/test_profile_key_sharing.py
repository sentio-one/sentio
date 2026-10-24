"""An admin can let a routed profile use the company's own model keys (private fork, FORK_DECISIONS D15).

Profiles are islands: a routed profile's secret scope is its own ``.env`` and nothing of the launch profile's, so a
sandboxed profile starts with no model key and its chat fails with "No usable credentials". Sharing is an admin-side
choice recorded in the launch home (never in the profile's own files, which the profile's user may be able to edit),
and it carries provider model keys only: never bot tokens, tool secrets or anything else in the launch environment.
"""
import json
from types import SimpleNamespace

import pytest

from agent.secret_scope import build_profile_secret_scope

KEY = "sk-company-commandcode"


@pytest.fixture
def company(tmp_path, monkeypatch):
    launch = tmp_path / "launch"
    (launch / "profiles" / "alice").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv("COMMANDCODE_API_KEY", KEY)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "company-bot-token")
    monkeypatch.setenv("UNRELATED_SETTING", "x")
    return SimpleNamespace(launch=launch, alice=launch / "profiles" / "alice")


def test_a_routed_profile_has_no_company_key_until_an_admin_shares_it(company):
    from gateway.profile_key_sharing import set_sharing

    assert "COMMANDCODE_API_KEY" not in build_profile_secret_scope(company.alice)
    set_sharing("alice", True)
    assert build_profile_secret_scope(company.alice)["COMMANDCODE_API_KEY"] == KEY
    set_sharing("alice", False)
    assert "COMMANDCODE_API_KEY" not in build_profile_secret_scope(company.alice)


def test_sharing_carries_provider_model_keys_only(company):
    from gateway.profile_key_sharing import set_sharing

    set_sharing("alice", True)
    scope = build_profile_secret_scope(company.alice)
    assert "TELEGRAM_BOT_TOKEN" not in scope and "UNRELATED_SETTING" not in scope


def test_keys_an_admin_put_in_the_launch_env_file_are_shared_too(company):
    from gateway.profile_key_sharing import set_sharing

    (company.launch / ".env").write_text("OPENROUTER_API_KEY=sk-or-company\n", encoding="utf-8")
    set_sharing("alice", True)
    assert build_profile_secret_scope(company.alice)["OPENROUTER_API_KEY"] == "sk-or-company"


def test_a_profiles_own_key_beats_the_shared_one(company):
    from gateway.profile_key_sharing import set_sharing

    (company.alice / ".env").write_text("COMMANDCODE_API_KEY=sk-alice-own\n", encoding="utf-8")
    set_sharing("alice", True)
    assert build_profile_secret_scope(company.alice)["COMMANDCODE_API_KEY"] == "sk-alice-own"


def test_sharing_one_profile_does_not_share_with_another(company):
    from gateway.profile_key_sharing import set_sharing

    (company.launch / "profiles" / "bob").mkdir()
    set_sharing("alice", True)
    assert "COMMANDCODE_API_KEY" not in build_profile_secret_scope(company.launch / "profiles" / "bob")


def test_the_default_profile_cannot_be_named_and_a_bad_name_is_refused(company):
    from gateway.profile_key_sharing import set_sharing

    for name in ("default", "../alice", "A l i c e", ""):
        with pytest.raises(ValueError):
            set_sharing(name, True)


def test_an_unreadable_sharing_file_shares_nothing(company):
    from gateway.profile_key_sharing import sharing_profiles

    (company.launch / "profile_key_sharing.json").write_text("{not json", encoding="utf-8")
    assert sharing_profiles() == frozenset()
    (company.launch / "profile_key_sharing.json").write_text(json.dumps({"profiles": "alice"}), encoding="utf-8")
    assert sharing_profiles() == frozenset()
