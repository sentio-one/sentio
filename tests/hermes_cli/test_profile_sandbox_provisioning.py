"""Profile provisioning under an access policy (SANDBOX_PLAN.md Phase 4, D13, O3)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import hermes_yaml

MANAGED_FILE = Path(__file__).resolve().parents[2] / "deploy" / "dokploy" / "managed" / "config.yaml"


def _setup_managed_company(tmp_path, monkeypatch):
    from hermes_cli import managed_scope

    launch = tmp_path / ".hermes"
    launch.mkdir(parents=True)
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "config.yaml").write_text(MANAGED_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    monkeypatch.setenv("HERMES_ADMIN_GROUP", "acme-admins")
    managed_scope.invalidate_managed_cache()
    return launch, managed


def test_create_profile_under_access_policy_stamps_docker_backend_and_prints_grant(tmp_path, monkeypatch, capsys):
    from hermes_cli import managed_scope, profile_cmd, profiles

    launch, _ = _setup_managed_company(tmp_path, monkeypatch)
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: launch)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: launch / "profiles")
    monkeypatch.setattr(profiles, "seed_profile_skills", lambda _dir, quiet=False: {"copied": []})
    monkeypatch.setattr(profiles, "_maybe_register_gateway_service", lambda _canon: None)
    monkeypatch.setattr(profiles, "_notify_multiplexer", lambda _canon: None)

    args = SimpleNamespace(
        profile_name="alice",
        clone=False,
        clone_all=False,
        no_alias=True,
        no_skills=True,
        clone_from=None,
        clone_channels=False,
        sync_imports=False,
        description="Alice profile",
    )
    profile_cmd._profile_create(args)
    managed_scope.invalidate_managed_cache()

    out = capsys.readouterr().out
    assert "hermes-profile-alice" in out
    assert "terminal.backend = docker" in out

    cfg_path = launch / "profiles" / "alice" / "config.yaml"
    assert cfg_path.is_file()
    cfg = hermes_yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    assert cfg.get("terminal", {}).get("backend") == "docker"


def test_create_profile_without_access_policy_leaves_default_backend_untouched(tmp_path, monkeypatch, capsys):
    from hermes_cli import managed_scope, profile_cmd, profiles

    launch = tmp_path / ".hermes"
    launch.mkdir(parents=True)
    empty_managed = tmp_path / "empty-managed"
    empty_managed.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(empty_managed))
    managed_scope.invalidate_managed_cache()

    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: launch)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: launch / "profiles")
    monkeypatch.setattr(profiles, "seed_profile_skills", lambda _dir, quiet=False: {"copied": []})
    monkeypatch.setattr(profiles, "_maybe_register_gateway_service", lambda _canon: None)
    monkeypatch.setattr(profiles, "_notify_multiplexer", lambda _canon: None)

    args = SimpleNamespace(
        profile_name="bob",
        clone=False,
        clone_all=False,
        no_alias=True,
        no_skills=True,
        clone_from=None,
        clone_channels=False,
        sync_imports=False,
        description="Bob profile",
    )
    profile_cmd._profile_create(args)

    out = capsys.readouterr().out
    assert "hermes-profile-bob" not in out
    cfg_path = launch / "profiles" / "bob" / "config.yaml"
    if cfg_path.is_file():
        cfg = hermes_yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        assert cfg.get("terminal", {}).get("backend") != "docker"
