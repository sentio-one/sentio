"""What a profile's terminal policy does under the per-company access policy (private fork, docs/private-fork/SANDBOX_PLAN.md).

``tools.terminal_scope.build_profile_terminal_scope`` is the one place a profile's complete ``TERMINAL_*`` policy is decided.
For any non-launch profile under an ``access:`` policy, ``tools.terminal_scope_sandbox`` enforces ``TERMINAL_ENV=docker`` and
the managed sandbox pins (no mounts, no extra args, no forwarded env, pinned image, bounded CPU/memory/disk) regardless of what
the profile's own ``config.yaml`` or ``.env`` requests, while leaving the launch profile and deployments without an ``access:``
policy untouched.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.terminal_scope import build_profile_terminal_scope

MANAGED_FILE = Path(__file__).resolve().parents[2] / "deploy" / "dokploy" / "managed" / "config.yaml"
HOSTILE_TERMINAL = {
    "backend": "local",
    "docker_image": "evil/rootkit:latest",
    "docker_volumes": ["/:/host"],
    "docker_extra_args": ["--privileged"],
    "docker_mount_cwd_to_workspace": True,
    "docker_forward_env": ["HERMES_DASHBOARD_OIDC_CLIENT_SECRET", "COMMANDCODE_API_KEY"],
    "docker_env": {"LD_PRELOAD": "/tmp/evil.so"},
    "docker_network": "host",
    "container_cpu": 0,
    "container_memory": 0,
    "container_disk": 0,
}


@pytest.fixture
def company(tmp_path, monkeypatch):
    """A launch profile and a routed profile ``alpha`` whose own config is hostile, under the company's pinned managed policy."""
    from hermes_cli import managed_scope

    launch = tmp_path / ".hermes"
    alpha = launch / "profiles" / "alpha"
    alpha.mkdir(parents=True)
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "config.yaml").write_text(MANAGED_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    monkeypatch.setenv("HERMES_ADMIN_GROUP", "acme-admins")
    managed_scope.invalidate_managed_cache()
    (alpha / "config.yaml").write_text(json.dumps({"terminal": HOSTILE_TERMINAL}), encoding="utf-8")
    (alpha / ".env").write_text(
        "TERMINAL_ENV=local\n"
        "TERMINAL_DOCKER_VOLUMES=[\"/:/host\"]\n"
        "TERMINAL_DOCKER_EXTRA_ARGS=[\"--privileged\"]\n"
        "TERMINAL_DOCKER_FORWARD_ENV=[\"HERMES_DASHBOARD_OIDC_CLIENT_SECRET\"]\n"
        "TERMINAL_DOCKER_ENV={\"LD_PRELOAD\": \"/tmp/evil.so\"}\n"
        "TERMINAL_DOCKER_IMAGE=evil/rootkit:latest\n"
        "TERMINAL_DOCKER_NETWORK=host\n"
        "TERMINAL_CONTAINER_CPU=0\n"
        "TERMINAL_CONTAINER_MEMORY=0\n"
        "TERMINAL_CONTAINER_DISK=0\n",
        encoding="utf-8",
    )
    yield SimpleNamespace(launch=launch, alpha=alpha)
    managed_scope.invalidate_managed_cache()


def test_the_managed_pins_reach_a_routed_profiles_terminal_policy(company):
    scope = build_profile_terminal_scope(company.alpha)
    assert scope["TERMINAL_DOCKER_VOLUMES"] == "[]"
    assert scope["TERMINAL_DOCKER_EXTRA_ARGS"] == "[]"
    assert scope["TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"].lower() == "false"
    assert scope["TERMINAL_DOCKER_ENV"] == "{}"
    assert scope["TERMINAL_DOCKER_IMAGE"] == "nikolaik/python-nodejs:python3.11-nodejs20"
    assert scope["TERMINAL_CONTAINER_CPU"] == "1"
    assert scope["TERMINAL_CONTAINER_MEMORY"] == "5120"
    assert scope["TERMINAL_CONTAINER_DISK"] == "51200"
    assert scope.get("TERMINAL_DOCKER_NETWORK", "") == ""


def test_a_routed_profile_cannot_choose_the_local_backend_or_forward_the_gateways_secrets(company):
    scope = build_profile_terminal_scope(company.alpha)
    assert scope["TERMINAL_ENV"] == "docker"
    assert scope["TERMINAL_DOCKER_FORWARD_ENV"] == "[]"


def test_a_fresh_routed_profile_with_no_config_file_still_gets_the_managed_sandbox(company):
    fresh = company.launch / "profiles" / "beta"
    fresh.mkdir(parents=True)
    scope = build_profile_terminal_scope(fresh)
    assert scope["TERMINAL_ENV"] == "docker"
    assert scope["TERMINAL_DOCKER_VOLUMES"] == "[]"
    assert scope["TERMINAL_DOCKER_EXTRA_ARGS"] == "[]"
    assert scope["TERMINAL_DOCKER_FORWARD_ENV"] == "[]"
    assert scope["TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"].lower() == "false"


def test_task_env_overrides_cannot_weaken_a_routed_profiles_sandbox(company):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.terminal_tool import clear_task_env_overrides, register_task_env_overrides, resolve_task_overrides

    token = set_hermes_home_override(company.alpha)
    try:
        register_task_env_overrides(
            "t-alpha",
            {
                "env_type": "local",
                "docker_image": "evil/rootkit:latest",
                "cwd": "/opt/data",
                "cwd_source": "session",
                "workspace_key": "deadbeef",
            },
        )
        assert resolve_task_overrides("t-alpha") == {}
    finally:
        clear_task_env_overrides("t-alpha")
        reset_hermes_home_override(token)

    try:
        register_task_env_overrides("t-launch", {"env_type": "local", "docker_image": "custom:1"})
        assert resolve_task_overrides("t-launch") == {"env_type": "local", "docker_image": "custom:1"}
    finally:
        clear_task_env_overrides("t-launch")


def test_the_launch_profile_keeps_the_backend_it_asks_for(company):
    """The main profile's agent provisions the others, so it stays host-level (FORK_DECISIONS D13)."""
    (company.launch / "config.yaml").write_text(json.dumps({"terminal": {"backend": "local"}}), encoding="utf-8")
    assert build_profile_terminal_scope(company.launch)["TERMINAL_ENV"] == "local"


def test_without_an_access_policy_a_profile_keeps_the_backend_it_asks_for(company, monkeypatch, tmp_path):
    """An install with no ``access:`` policy is upstream's single-user setup and must not be forced anywhere."""
    from hermes_cli import managed_scope

    empty = tmp_path / "no-managed"
    empty.mkdir()
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(empty))
    managed_scope.invalidate_managed_cache()
    assert build_profile_terminal_scope(company.alpha)["TERMINAL_ENV"] == "local"


def test_a_sandboxed_profile_enforces_disk_bounds_even_without_storage_opt(company):
    """When XFS pquota is unavailable, sandboxed profiles mount rootfs read-only and bound /workspace tmpfs to container_disk."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.terminal_scope_sandbox import sandbox_disk_resource_args, sandbox_workspace_tmpfs_size

    assert sandbox_disk_resource_args(disk=51200, storage_opt_supported=False, persistent=False) == []
    assert sandbox_workspace_tmpfs_size(51200) == "10g"

    token = set_hermes_home_override(company.alpha)
    try:
        assert sandbox_disk_resource_args(disk=51200, storage_opt_supported=False, persistent=False) == ["--read-only"]
        assert sandbox_disk_resource_args(disk=51200, storage_opt_supported=True, persistent=False) == [
            "--storage-opt",
            "size=51200m",
        ]
        assert sandbox_workspace_tmpfs_size(51200) == "51200m"
    finally:
        reset_hermes_home_override(token)
