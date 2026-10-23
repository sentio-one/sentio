"""What a profile's terminal policy does under the per-company access policy (private fork, docs/private-fork/SANDBOX_PLAN.md).

``tools.terminal_scope.build_profile_terminal_scope`` is the one place a profile's complete ``TERMINAL_*`` policy is decided, and
it reads the profile's own ``config.yaml`` and ``.env`` raw. Verified 2026-10-23: it ignores the managed overlay, so the pinned
``terminal.docker_*`` keys in ``deploy/dokploy/managed/config.yaml`` do not reach a routed profile's sandbox, and the profile can
pick the local backend (code execution in the gateway container) or forward the gateway's secrets into its container.

The two strict ``xfail`` tests are the contract SANDBOX_PLAN.md phase 1 must meet. ``strict`` makes them fail the day they start
passing, so the markers get removed with the fix instead of rotting. The other two pass today and must keep passing: the launch
profile keeps its own backend, and a deployment with no access policy behaves like upstream.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.terminal_scope import build_profile_terminal_scope

MANAGED_FILE = Path(__file__).resolve().parents[2] / "deploy" / "dokploy" / "managed" / "config.yaml"
HOSTILE_TERMINAL = {
    "backend": "local",
    "docker_volumes": ["/:/host"],
    "docker_extra_args": ["--privileged"],
    "docker_mount_cwd_to_workspace": True,
    "docker_forward_env": ["HERMES_DASHBOARD_OIDC_CLIENT_SECRET", "COMMANDCODE_API_KEY"],
}
PHASE_1 = "SANDBOX_PLAN.md phase 1: enforce the sandbox policy in build_profile_terminal_scope"


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
    (alpha / ".env").write_text("TERMINAL_ENV=local\n", encoding="utf-8")
    yield SimpleNamespace(launch=launch, alpha=alpha)
    managed_scope.invalidate_managed_cache()


@pytest.mark.xfail(strict=True, reason=PHASE_1)
def test_the_managed_pins_reach_a_routed_profiles_terminal_policy(company):
    scope = build_profile_terminal_scope(company.alpha)
    assert scope["TERMINAL_DOCKER_VOLUMES"] == "[]"
    assert scope["TERMINAL_DOCKER_EXTRA_ARGS"] == "[]"
    assert scope["TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"].lower() == "false"


@pytest.mark.xfail(strict=True, reason=PHASE_1)
def test_a_routed_profile_cannot_choose_the_local_backend_or_forward_the_gateways_secrets(company):
    scope = build_profile_terminal_scope(company.alpha)
    assert scope["TERMINAL_ENV"] == "docker"
    assert scope["TERMINAL_DOCKER_FORWARD_ENV"] == "[]"


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
