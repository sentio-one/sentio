"""Host-execution surfaces outside the terminal backend (SANDBOX_PLAN.md §3 G5, Phase 2).

For any non-launch profile under an ``access:`` policy, host-level execution paths outside the
Docker terminal backend must fail closed and log:
1. ``mcp_servers`` stdio commands (HTTP/SSE MCP remains allowed)
2. ``quick_commands`` with ``type: exec``
3. Shell hooks (``hooks:`` / ``hooks_auto_accept``)
4. Host-local browser launch (``launch_chromium``, ``launch_lightpanda``, ``bot_desktop.placement: gateway``)
5. Cron ``script`` and ``monitor_script`` jobs (creation and execution)

The launch profile (D13) and deployments without an ``access:`` policy keep upstream behavior.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_constants import reset_hermes_home_override, set_hermes_home_override

MANAGED_FILE = Path(__file__).resolve().parents[2] / "deploy" / "dokploy" / "managed" / "config.yaml"


@pytest.fixture
def company(tmp_path, monkeypatch):
    from hermes_cli import managed_scope

    launch = tmp_path / ".hermes"
    alpha = launch / "profiles" / "alpha"
    launch.mkdir(parents=True)
    alpha.mkdir(parents=True)
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "config.yaml").write_text(MANAGED_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    monkeypatch.setenv("HERMES_ADMIN_GROUP", "acme-admins")
    managed_scope.invalidate_managed_cache()
    yield SimpleNamespace(launch=launch, alpha=alpha, managed=managed)
    managed_scope.invalidate_managed_cache()


def _disable_access_policy(tmp_path, monkeypatch):
    from hermes_cli import managed_scope

    empty = tmp_path / "empty-managed"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(empty))
    managed_scope.invalidate_managed_cache()


def test_stdio_mcp_servers_are_refused_for_sandboxed_profile_while_http_stays_allowed(company, caplog):
    from tools.mcp_tool_config import _load_mcp_config

    mcp_cfg = {
        "mcp_servers": {
            "host_shell": {"command": "/bin/sh", "args": ["-c", "id"]},
            "remote_http": {"url": "https://mcp.example.com/sse"},
        }
    }
    (company.alpha / "config.yaml").write_text(json.dumps(mcp_cfg), encoding="utf-8")
    (company.launch / "config.yaml").write_text(json.dumps(mcp_cfg), encoding="utf-8")

    token = set_hermes_home_override(company.alpha)
    try:
        with caplog.at_level(logging.WARNING, logger="tools.mcp_tool"):
            loaded_alpha = _load_mcp_config()
    finally:
        reset_hermes_home_override(token)

    assert "host_shell" not in loaded_alpha
    assert "remote_http" in loaded_alpha
    assert any("Skipping stdio MCP server 'host_shell'" in r.message for r in caplog.records)

    # Launch profile keeps stdio MCP servers
    loaded_launch = _load_mcp_config()
    assert "host_shell" in loaded_launch
    assert "remote_http" in loaded_launch


def test_stdio_mcp_servers_are_allowed_when_no_access_policy_is_configured(company, tmp_path, monkeypatch):
    from tools.mcp_tool_config import _load_mcp_config

    _disable_access_policy(tmp_path, monkeypatch)
    (company.alpha / "config.yaml").write_text(
        json.dumps({"mcp_servers": {"host_shell": {"command": "/bin/echo", "args": ["ok"]}}}),
        encoding="utf-8",
    )
    token = set_hermes_home_override(company.alpha)
    try:
        loaded = _load_mcp_config()
    finally:
        reset_hermes_home_override(token)
    assert "host_shell" in loaded


def test_quick_command_exec_is_refused_for_sandboxed_profile(company, tmp_path, monkeypatch, caplog):
    from gateway.run_inbound_hooks import GatewayInboundHooksMixin

    marker = tmp_path / "pwned"

    class DummyRunner(GatewayInboundHooksMixin):
        def __init__(self, home: Path):
            self.hermes_home = home

    alpha_runner = DummyRunner(company.alpha)
    with caplog.at_level(logging.WARNING):
        out = asyncio.run(alpha_runner._hm_run_exec_quick_command("pwn", f"touch {marker}"))
    assert "refused" in out.lower()
    assert not marker.exists()
    assert any("Quick command 'pwn' (type: exec) refused" in r.message for r in caplog.records)

    # Launch profile can still run exec quick commands
    launch_runner = DummyRunner(company.launch)
    out_launch = asyncio.run(launch_runner._hm_run_exec_quick_command("ok", "printf launch-ok"))
    assert out_launch == "launch-ok"

    # Without access policy, profile can run exec quick commands
    _disable_access_policy(tmp_path, monkeypatch)
    out_no_policy = asyncio.run(alpha_runner._hm_run_exec_quick_command("ok", "printf unlocked-ok"))
    assert out_no_policy == "unlocked-ok"


def test_shell_hooks_are_refused_for_sandboxed_profile(company, tmp_path, monkeypatch, caplog):
    from agent.shell_hooks import _hook_specs

    cfg = {
        "hooks_auto_accept": True,
        "hooks": {"pre_tool_call": [{"command": "touch /tmp/evil"}]},
    }
    token = set_hermes_home_override(company.alpha)
    try:
        with caplog.at_level(logging.WARNING):
            specs, complete = _hook_specs(cfg)
    finally:
        reset_hermes_home_override(token)

    assert specs == []
    assert complete is True
    assert any("Shell hooks refused for sandboxed profile" in r.message for r in caplog.records)

    # Launch profile parses hooks normally
    launch_specs, _ = _hook_specs(cfg)
    assert len(launch_specs) == 1

    # Without access policy, profile parses hooks normally
    _disable_access_policy(tmp_path, monkeypatch)
    token = set_hermes_home_override(company.alpha)
    try:
        unlocked_specs, _ = _hook_specs(cfg)
    finally:
        reset_hermes_home_override(token)
    assert len(unlocked_specs) == 1


def test_local_host_browser_is_refused_for_sandboxed_profile(company, caplog):
    from tools.bot_desktop import placement
    from tools.browser_launcher import launch_chromium
    from tools.browser_lightpanda import launch_lightpanda
    from tools.terminal_scope import install_and_reset_profile_terminal_scope

    (company.alpha / "config.yaml").write_text(
        json.dumps({"bot_desktop": {"placement": "gateway"}}),
        encoding="utf-8",
    )
    token = set_hermes_home_override(company.alpha)
    try:
        with install_and_reset_profile_terminal_scope(company.alpha):
            with caplog.at_level(logging.WARNING):
                # Even when the profile asks for gateway placement, it resolves to terminal (inside container)
                resolved = placement.resolve()
                assert resolved.where == placement.TERMINAL
                assert resolved.backend == "docker"

                browser, err = launch_chromium("test-key")
                assert browser is None
                assert "disabled for sandboxed profiles" in (err or "")

                lp, lp_err = launch_lightpanda("test-lp")
                assert lp is None
                assert "disabled for sandboxed profiles" in (lp_err or "")
    finally:
        reset_hermes_home_override(token)


def test_cron_script_and_monitor_script_are_refused_for_sandboxed_profile(company, tmp_path, monkeypatch):
    from cron.jobs import create_job
    from cron.scheduler_script import _run_job_script

    scripts_dir = company.alpha / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script_file = scripts_dir / "check.sh"
    script_file.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script_file.chmod(0o755)

    token = set_hermes_home_override(company.alpha)
    try:
        with pytest.raises(ValueError, match="disabled for sandboxed profiles"):
            create_job(prompt="run", schedule="every 1h", script="check.sh")

        with pytest.raises(ValueError, match="disabled for sandboxed profiles"):
            create_job(prompt="watch", schedule="every 1h", monitor_script="check.sh")

        ok, output = _run_job_script("check.sh")
        assert ok is False
        assert "disabled for sandboxed profiles" in output

        # Prompt-only cron jobs still work for sandboxed profiles
        prompt_job = create_job(prompt="Summarize status", schedule="every 1h")
        assert prompt_job["id"]
    finally:
        reset_hermes_home_override(token)

    # Launch profile can create script jobs
    launch_scripts = company.launch / "scripts"
    launch_scripts.mkdir(parents=True, exist_ok=True)
    (launch_scripts / "check.sh").write_text("#!/bin/sh\necho launch-ok\n", encoding="utf-8")
    launch_job = create_job(prompt="run", schedule="every 1h", script="check.sh")
    assert launch_job["script"] == "check.sh"

    # Without access policy, non-launch profile can create script jobs
    _disable_access_policy(tmp_path, monkeypatch)
    token = set_hermes_home_override(company.alpha)
    try:
        unlocked_job = create_job(prompt="run", schedule="every 1h", script="check.sh")
        assert unlocked_job["script"] == "check.sh"
    finally:
        reset_hermes_home_override(token)
