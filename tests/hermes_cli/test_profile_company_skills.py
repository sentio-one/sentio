"""A profile created under the company access policy gets the business skill baseline, and ``trim-skills`` fixes older
ones (private fork, D16). The real seeding subprocess runs under the real managed policy."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.skill_utils import ESSENTIAL_SKILLS
from tools.skills_sync_company import DEFAULT_COMPANY_BASELINE

MANAGED_FILE = Path(__file__).resolve().parents[2] / "deploy" / "dokploy" / "managed" / "config.yaml"


@pytest.fixture
def company(tmp_path, monkeypatch):
    from hermes_cli import managed_scope, profiles

    launch, managed = tmp_path / ".hermes", tmp_path / "managed"
    launch.mkdir()
    managed.mkdir()
    (managed / "config.yaml").write_text(MANAGED_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    monkeypatch.setenv("HERMES_ADMIN_EMAILS", "admin@acme.example")
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: launch)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: launch / "profiles")
    monkeypatch.setattr(profiles, "_maybe_register_gateway_service", lambda _canon: None)
    monkeypatch.setattr(profiles, "_notify_multiplexer", lambda _canon: None)
    managed_scope.invalidate_managed_cache()
    yield launch
    managed_scope.invalidate_managed_cache()


def _create(name):
    from hermes_cli import profile_cmd

    profile_cmd._profile_create(SimpleNamespace(
        profile_name=name, clone=False, clone_all=False, no_alias=True, no_skills=False, clone_from=None,
        clone_channels=False, sync_imports=False, description=None, share_keys=False))


def _installed(home):
    return {md.parent.name for md in (home / "skills").rglob("SKILL.md")}


def test_a_new_company_profile_is_seeded_with_the_baseline_only(company):
    _create("alice")
    installed = _installed(company / "profiles" / "alice")
    assert ESSENTIAL_SKILLS <= installed <= ESSENTIAL_SKILLS | DEFAULT_COMPANY_BASELINE
    assert "profile-provisioning" not in installed and "claude-code" not in installed


def test_trim_skills_cuts_an_overstocked_profile_down(company, monkeypatch, capsys):
    from hermes_cli import profile_skills
    from tools import skills_sync_company
    from tools.skills_sync import sync_skills

    _create("alice")
    home = company / "profiles" / "alice"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(skills_sync_company, "is_sandboxed_profile", lambda _h=None: False)
    sync_skills(quiet=True)  # an older profile: every bundled skill
    monkeypatch.setenv("HERMES_HOME", str(company))
    assert "claude-code" in _installed(home)
    profile_skills.profile_trim_skills(SimpleNamespace(profile_name="alice"))
    installed = _installed(home)
    assert ESSENTIAL_SKILLS <= installed <= ESSENTIAL_SKILLS | DEFAULT_COMPANY_BASELINE
    assert "removed" in capsys.readouterr().out


def test_trim_skills_refuses_the_default_profile_and_a_missing_one(company):
    from hermes_cli import profile_skills

    for name in ("default", "ghost"):
        with pytest.raises(SystemExit):
            profile_skills.profile_trim_skills(SimpleNamespace(profile_name=name))


def test_the_cli_exposes_trim_skills():
    import argparse

    from hermes_cli.profile_cmd import PROFILE_ACTIONS
    from hermes_cli.subcommands.profile import build_profile_parser

    parser = argparse.ArgumentParser()
    build_profile_parser(parser.add_subparsers(dest="command"), cmd_profile=lambda _a: None)
    args = parser.parse_args(["profile", "trim-skills", "alice"])
    assert args.profile_action == "trim-skills" and args.profile_name == "alice"
    assert "trim-skills" in PROFILE_ACTIONS
