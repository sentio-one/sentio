"""A sandboxed company profile is seeded with a small business baseline, not every bundled skill (private fork, D16).

The launch profile (the admin's) keeps the full set, including ``profile-provisioning``, which has no business in a
customer's profile. Real bundled skills, real sync, real profile directory.
"""
import json

import pytest

from agent.skill_utils import ESSENTIAL_SKILLS
from tools import skills_sync_company
from tools.skills_sync import sync_skills

BASELINE = skills_sync_company.DEFAULT_COMPANY_BASELINE


def _installed(home):
    return {md.parent.name for md in (home / "skills").rglob("SKILL.md")}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    home = tmp_path / "profiles" / "sales"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _sandboxed(monkeypatch, value=True):
    monkeypatch.setattr(skills_sync_company, "is_sandboxed_profile", lambda _home=None: value)


def test_a_sandboxed_profile_gets_the_essentials_and_the_baseline_only(profile, monkeypatch):
    _sandboxed(monkeypatch)
    sync_skills(quiet=True)
    installed = _installed(profile)
    assert installed == (ESSENTIAL_SKILLS | BASELINE) & installed and ESSENTIAL_SKILLS <= installed
    assert installed >= {"docx", "pdf", "xlsx"}
    for unwanted in ("profile-provisioning", "claude-code", "ascii-video", "songwriting-and-ai-music", "p5js"):
        assert unwanted not in installed


def test_a_second_sync_does_not_bring_the_rest_back(profile, monkeypatch):
    _sandboxed(monkeypatch)
    sync_skills(quiet=True)
    first = _installed(profile)
    sync_skills(quiet=True)
    assert _installed(profile) == first


def test_the_launch_profile_keeps_every_bundled_skill(profile, monkeypatch):
    _sandboxed(monkeypatch, False)
    sync_skills(quiet=True)
    installed = _installed(profile)
    assert {"profile-provisioning", "claude-code", "docx"} <= installed and len(installed) > 40


def test_the_baseline_can_be_set_in_config_but_provisioning_is_never_in_it(profile, monkeypatch):
    _sandboxed(monkeypatch)
    (profile / "config.yaml").write_text(
        json.dumps({"skills": {"company_baseline": ["pdf", "profile-provisioning"]}}), encoding="utf-8")
    sync_skills(quiet=True)
    assert _installed(profile) == ESSENTIAL_SKILLS | {"pdf"}


def test_trimming_an_overstocked_profile_keeps_the_baseline_and_anything_edited(profile, monkeypatch):
    _sandboxed(monkeypatch, False)
    sync_skills(quiet=True)
    edited = next(md for md in (profile / "skills").rglob("SKILL.md") if md.parent.name == "ascii-video")
    edited.write_text(edited.read_text(encoding="utf-8") + "\nmy own note\n", encoding="utf-8")
    _sandboxed(monkeypatch)
    result = skills_sync_company.trim_to_company_baseline()
    installed = _installed(profile)
    assert installed == ESSENTIAL_SKILLS | (BASELINE & installed) | {"ascii-video"}
    assert "claude-code" not in installed and "profile-provisioning" not in installed and "ascii-video" in installed
    assert "claude-code" in result["removed"]
