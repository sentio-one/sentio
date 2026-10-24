"""``hermes profile create --share-keys`` and ``hermes profile share-keys`` (private fork, FORK_DECISIONS D15).

The command only records the admin's choice in the launch home; it never reads or prints a key, so it works from the
main agent's terminal, where provider keys are deliberately not visible.
"""
from types import SimpleNamespace

import pytest


@pytest.fixture
def launch(tmp_path, monkeypatch):
    from hermes_cli import profiles

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("COMMANDCODE_API_KEY", "sk-company")
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: home / "profiles")
    monkeypatch.setattr(profiles, "seed_profile_skills", lambda _dir, quiet=False: {"copied": []})
    monkeypatch.setattr(profiles, "_maybe_register_gateway_service", lambda _canon: None)
    monkeypatch.setattr(profiles, "_notify_multiplexer", lambda _canon: None)
    return home


def _create(name, **flags):
    from hermes_cli import profile_cmd

    base = dict(profile_name=name, clone=False, clone_all=False, no_alias=True, no_skills=True, clone_from=None,
                clone_channels=False, sync_imports=False, description=None, share_keys=False)
    profile_cmd._profile_create(SimpleNamespace(**{**base, **flags}))


def test_create_with_share_keys_lets_the_profile_use_the_company_keys(launch, capsys):
    from agent.secret_scope import build_profile_secret_scope

    _create("alice", share_keys=True)
    assert build_profile_secret_scope(launch / "profiles" / "alice")["COMMANDCODE_API_KEY"] == "sk-company"
    out = capsys.readouterr().out
    assert "company's model keys" in out and "sk-company" not in out


def test_create_without_it_says_how_the_profile_gets_keys_and_shares_nothing(launch, capsys):
    from agent.secret_scope import build_profile_secret_scope

    _create("alice")
    assert "COMMANDCODE_API_KEY" not in build_profile_secret_scope(launch / "profiles" / "alice")
    out = capsys.readouterr().out
    assert "shell environment" not in out
    assert "--share-keys" in out and "alice setup" in out


def test_share_keys_turns_sharing_on_and_off_for_an_existing_profile(launch, capsys):
    from agent.secret_scope import build_profile_secret_scope
    from hermes_cli import profile_keys

    _create("alice")
    profile_keys.profile_share_keys(SimpleNamespace(profile_name="alice", off=False))
    assert "COMMANDCODE_API_KEY" in build_profile_secret_scope(launch / "profiles" / "alice")
    profile_keys.profile_share_keys(SimpleNamespace(profile_name="alice", off=True))
    assert "COMMANDCODE_API_KEY" not in build_profile_secret_scope(launch / "profiles" / "alice")


def test_share_keys_refuses_a_profile_that_does_not_exist_and_the_default(launch):
    from hermes_cli import profile_keys

    for name in ("ghost", "default"):
        with pytest.raises(SystemExit):
            profile_keys.profile_share_keys(SimpleNamespace(profile_name=name, off=False))


def test_show_reports_whose_keys_the_profile_uses(launch, capsys):
    from hermes_cli import profile_cmd

    _create("alice", share_keys=True)
    _create("bob")
    capsys.readouterr()
    profile_cmd._profile_show(SimpleNamespace(profile_name="alice"))
    profile_cmd._profile_show(SimpleNamespace(profile_name="bob"))
    out = capsys.readouterr().out
    assert out.count("company's model keys") == 1 and "its own" in out
