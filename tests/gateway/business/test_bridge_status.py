"""`messages status` reports the bridge's real connection, not an empty table's default.

`business_bridge_states` is only ever written on a terminal re-auth, so for a healthy account it has no
row and the status used to read "disconnected" while the bridge was connected and delivering.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.business.models import AccountKey
from gateway.business.readiness import bridge_status, record_bridge_state
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


ACCOUNT = AccountKey(transport_profile="anika", account_id="anika")


def test_a_connected_bridge_with_no_recorded_state_reads_connected(store):
    adapter = SimpleNamespace(is_connected=True, fatal_error_code=None)
    state = bridge_status(ACCOUNT, store=store, adapter=adapter)
    assert state.state == "connected"
    assert state.code is None


def test_a_bridge_that_is_down_reads_disconnected_with_the_reason(store):
    adapter = SimpleNamespace(is_connected=False, fatal_error_code="whatsapp_bridge_exited")
    state = bridge_status(ACCOUNT, store=store, adapter=adapter)
    assert state.state == "disconnected"
    assert state.code == "whatsapp_bridge_exited"


def test_a_terminal_logout_wins_over_a_live_connection(store):
    """needs_reauth is the one recorded state that must never be hidden by a connected socket."""
    record_bridge_state(ACCOUNT, state="needs_reauth", code="401_logged_out", store=store)
    adapter = SimpleNamespace(is_connected=True, fatal_error_code=None)
    assert bridge_status(ACCOUNT, store=store, adapter=adapter).state == "needs_reauth"


def test_with_no_adapter_and_nothing_recorded_the_state_is_unknown_not_disconnected(store):
    assert bridge_status(ACCOUNT, store=store, adapter=None).state == "unknown"


def test_with_no_adapter_the_recorded_state_is_used(store):
    record_bridge_state(ACCOUNT, state="connected", store=store)
    assert bridge_status(ACCOUNT, store=store, adapter=None).state == "connected"


@pytest.mark.asyncio
async def test_the_status_operation_uses_the_live_adapter(store, monkeypatch):
    from gateway.business import operator_operations as ops

    with store._connect() as db:
        db.execute("INSERT OR REPLACE INTO business_accounts(runtime_profile,transport_profile,account_id) VALUES('anika','anika','anika')")
    adapter = SimpleNamespace(is_connected=True, fatal_error_code=None)
    monkeypatch.setattr("gateway.business.worker.delivery_adapter", lambda runner, account: adapter)
    service = SimpleNamespace(store=store, runner=object())
    principal = SimpleNamespace(runtime_profile="anika", permissions=frozenset({"admin"}), actor_type="human")
    result = await ops._status(service, {"runtime_profile": "anika"}, principal)
    assert result["accounts"][0]["bridge"].state == "connected"
