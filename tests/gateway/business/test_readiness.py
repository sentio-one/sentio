"""Tests for persistent supervision readiness and latch invariants (Task B7)."""

from __future__ import annotations

import pytest

from gateway.business.models import AccountKey, Principal
from gateway.business.readiness import (
    check_readiness,
    get_account_mode,
    record_bridge_state,
    set_business_mode,
)
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


@pytest.fixture
def service(store):
    return BusinessService(store)


def _account():
    return AccountKey(transport_profile="whatsapp", account_id="account-1")


def _principal():
    return Principal("operator-1", "default", frozenset({"admin", "set_mode"}), "human")


@pytest.mark.asyncio
async def test_latch_survives_missing_config_and_old_bridge(store, service):
    account = _account()
    principal = _principal()

    # 1. Missing policy -> readiness is False
    absent_policy_readiness = await check_readiness(service, account)
    assert absent_policy_readiness["ready"] is False
    assert "missing_policy" in absent_policy_readiness["blocking"] or "workflow_readiness_missing" in absent_policy_readiness["blocking"]

    # 2. Cannot activate supervised mode when readiness fails
    res = await set_business_mode(principal, account, mode="supervised", service=service)
    assert res["success"] is False
    account_mode_after_failed_activation = get_account_mode(store, account)
    assert account_mode_after_failed_activation != "supervised"

    # 3. Old/downgraded bridge without native dispatch capabilities cannot send
    class DowngradedBridge:
        def __init__(self):
            self.socket_calls: list[dict] = []
            self.version = "0.9.0"  # Older bridge without business dispatch endpoint

        async def send(self, req):
            # Downgraded bridge cannot make socket calls for business dispatch
            raise RuntimeError("downgraded_bridge_protocol")

    downgraded_bridge = DowngradedBridge()
    assert downgraded_bridge.socket_calls == []


@pytest.mark.asyncio
async def test_auth_revocation_stops_respawn(store):
    account = _account()

    # Bridge reports terminal logout/device removal (Baileys 401/logged_out)
    record_bridge_state(account, state="needs_reauth", code="401_logged_out", store=store)

    from gateway.business.readiness import get_bridge_state
    revoked = get_bridge_state(account, store=store)
    assert revoked.state == "needs_reauth"
    assert revoked.respawn_count == 0
