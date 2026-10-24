"""The per-company deployment pins its access policy in the managed scope (private fork, deploy/dokploy).

Real loaders over real files: ``deploy/dokploy/managed/config.yaml`` is mounted at ``$HERMES_MANAGED_DIR`` in the
container, its ``${VAR}`` refs resolve against the container environment, and what the profile's own
``config.yaml`` (which an agent or a granted user might edit) says cannot widen it.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from gateway.config import load_gateway_config

REPO = Path(__file__).resolve().parents[2]
MANAGED_FILE = REPO / "deploy" / "dokploy" / "managed" / "config.yaml"
HEALTHCHECK = REPO / "deploy" / "dokploy" / "healthcheck.py"
ISSUER = "https://sso.acme.example"


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    from hermes_cli import managed_scope

    home, managed = tmp_path / "home", tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    (managed / "config.yaml").write_text(MANAGED_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    monkeypatch.setenv("HERMES_ADMIN_GROUP", "acme-admins")
    managed_scope.invalidate_managed_cache()
    yield SimpleDeployment(home, managed)
    managed_scope.invalidate_managed_cache()


class SimpleDeployment:
    def __init__(self, home, managed):
        self.home, self.managed = home, managed

    def user_config(self, data):
        (self.home / "config.yaml").write_text(json.dumps(data), encoding="utf-8")  # JSON is valid YAML


def _grant(policy, sub="u1", groups=(), provider="self-hosted"):
    return policy.grant_for(provider=provider, issuer=ISSUER, sub=sub, groups=tuple(groups))


def test_the_managed_file_turns_the_policy_on_from_the_environment(deployment):
    policy = load_gateway_config().access
    assert policy.configured is True
    assert _grant(policy, groups=["acme-admins"]).admin is True
    assert _grant(policy, groups=["hermes-profile-alpha"]).profiles == {"alpha"}
    assert _grant(policy, groups=["something-else"]).profiles == frozenset()


def test_an_unset_admin_group_fails_closed_not_open(deployment, monkeypatch):
    monkeypatch.delenv("HERMES_ADMIN_GROUP")
    policy = load_gateway_config().access
    assert policy.configured is True
    assert _grant(policy, groups=["acme-admins", ""]).admin is False
    assert _grant(policy, groups=["hermes-profile-alpha"]).profiles == {"alpha"}


def test_the_profiles_own_config_cannot_add_admins_users_or_groups(deployment):
    """An agent or a granted user can edit the launch profile's config.yaml; the pinned policy must not move."""
    deployment.user_config({"access": {
        "admin_groups": ["evil-admins"], "profile_group_prefix": "",
        "admins": [{"provider": "self-hosted", "issuer": ISSUER, "sub": "mallory"}],
        "users": [{"provider": "self-hosted", "issuer": ISSUER, "sub": "mallory", "profiles": ["default"]}],
        "group_providers": ["nous"]}})
    policy = load_gateway_config().access
    assert _grant(policy, sub="mallory").admin is False
    assert _grant(policy, sub="mallory").profiles == frozenset()
    assert _grant(policy, groups=["evil-admins"]).admin is False
    assert _grant(policy, groups=["acme-admins"]).admin is True
    assert _grant(policy, groups=["hermes-profile-alpha"]).profiles == {"alpha"}
    assert _grant(policy, groups=["hermes-admins"], provider="nous").admin is False


def test_without_the_managed_file_there_is_no_policy(tmp_path, monkeypatch):
    from hermes_cli import managed_scope

    (tmp_path / "home").mkdir()
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(tmp_path / "empty"))
    managed_scope.invalidate_managed_cache()
    assert load_gateway_config().access.configured is False


def _healthcheck():
    spec = importlib.util.spec_from_file_location("dokploy_healthcheck", HEALTHCHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_healthcheck_is_green_only_while_the_policy_is_active(deployment):
    check = _healthcheck()
    assert check.policy_active() is True
    (deployment.managed / "config.yaml").write_text("access: [this is: not valid yaml", encoding="utf-8")
    from hermes_cli import managed_scope

    managed_scope.invalidate_managed_cache()
    assert check.policy_active() is False  # a malformed managed file is fail-open upstream; the container turns unhealthy


def test_the_healthcheck_reports_a_dead_dashboard_port(deployment):
    check = _healthcheck()
    assert check.port_open(1) is False
    assert check.main(port=1) == 1


def test_a_profile_cannot_loosen_the_docker_sandbox_from_its_own_config(deployment):
    """Scope: the overlay applied to a config dict, as ``load_config`` does. A routed profile's terminal policy is
    enforced in ``build_profile_terminal_scope`` via ``tools.terminal_scope_sandbox`` (tests/tools/test_terminal_scope_sandbox_gap.py)."""
    from hermes_cli.managed_scope import apply_managed_overlay

    config = apply_managed_overlay({"terminal": {
        "backend": "docker", "docker_mount_cwd_to_workspace": True,
        "docker_volumes": ["/:/host"], "docker_extra_args": ["--privileged"],
        "docker_forward_env": ["OPENROUTER_API_KEY"], "docker_env": {"LD_PRELOAD": "/tmp/x.so"},
        "docker_image": "mine:latest", "container_cpu": 0, "container_memory": 0, "container_disk": 0}})
    terminal = config["terminal"]
    assert terminal["docker_mount_cwd_to_workspace"] is False
    assert terminal["docker_volumes"] == [] and terminal["docker_extra_args"] == []
    assert terminal["docker_forward_env"] == [] and terminal["docker_env"] == {}
    assert terminal["docker_image"] == "nikolaik/python-nodejs:python3.11-nodejs20"
    assert terminal["container_cpu"] == 1 and terminal["container_memory"] == 5120 and terminal["container_disk"] == 51200
    assert terminal["backend"] == "docker"  # backend stays unpinned in managed overlay so launch profile stays local
