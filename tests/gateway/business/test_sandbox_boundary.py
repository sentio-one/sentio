"""Approval authority over the same-user socket depends on the agent running off the host."""

import asyncio

import pytest
import pytest_asyncio

from gateway.business.client import request_business
from gateway.business.control import register_business_control_handlers
from gateway.business.models import AccountKey
from gateway.business.readiness import _set_account_mode_row
from gateway.business.runtime_readiness import probe_runtime
from gateway.business.sandbox_boundary import agent_tools_sandboxed
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.control_socket import GatewayControlServer

ACCOUNT = AccountKey("default", "default")


@pytest_asyncio.fixture
async def supervised(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = BusinessStore(tmp_path)
    _set_account_mode_row(store, ACCOUNT, "supervised")
    service = BusinessService(store)
    service.runtime_profile = "default"
    server = GatewayControlServer(tmp_path)
    register_business_control_handlers(server, service, loop=asyncio.get_running_loop())
    assert await server.start()
    yield tmp_path, service
    await server.stop()


def _sandbox(home):
    (home / "config.yaml").write_text("terminal:\n  backend: docker\n", encoding="utf-8")


async def _call(home, operation, params=None):
    return await asyncio.to_thread(request_business, home, operation=operation, params=params or {})


@pytest.mark.asyncio
async def test_host_level_supervised_profile_cannot_authorize_over_the_socket(supervised):
    home, _ = supervised
    for operation in ("operator.approve", "operator.resume", "operator.set_mode", "operator.edit"):
        result = await _call(home, operation, {"revision_id": "r", "plan_digest": "d", "mode": "off"})
        assert result == {"ok": False, "error": "sandbox_required_for_socket_operation"}, operation
    # Operations that can only inspect or reduce sending stay available.
    assert (await _call(home, "operator.pause", {"reason": "hold"})).get("error") != "sandbox_required_for_socket_operation"
    assert (await _call(home, "operator.status"))["ok"] is True


@pytest.mark.asyncio
async def test_sandboxed_profile_reaches_the_operation(supervised):
    home, _ = supervised
    _sandbox(home)
    result = await _call(home, "operator.approve", {"revision_id": "missing", "plan_digest": "d"})
    assert result["error"] != "sandbox_required_for_socket_operation"


@pytest.mark.asyncio
async def test_readiness_blocks_until_agent_tools_are_sandboxed(supervised):
    home, service = supervised
    assert not agent_tools_sandboxed(service, "default")
    assert "agent_tools_not_sandboxed" in await probe_runtime(service, ACCOUNT, "default")
    _sandbox(home)
    assert agent_tools_sandboxed(service, "default")
    assert "agent_tools_not_sandboxed" not in await probe_runtime(service, ACCOUNT, "default")
