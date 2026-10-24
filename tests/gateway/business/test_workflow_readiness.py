"""Tests for native WhatsApp workflow readiness and pilot handoff (Task C7)."""

from __future__ import annotations

import pytest

from gateway.business.config import BusinessConfig
from gateway.business.migration import CutoverManifest
from gateway.business.models import AccountKey, Scope
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.business.workflow_readiness import (
    check_workflow_readiness,
    pilot_report,
)


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


def _account():
    return AccountKey(transport_profile="whatsapp", account_id="account-1")


def _scope():
    return Scope("default", "whatsapp", "account-1", "27800000001@s.whatsapp.net")


def _valid_config():
    return BusinessConfig(
        mode="supervised",
        required_policies=("content_policy",),
        operators=("operator-1",),
        rewrite_attempts=2,
        approval_ttl_seconds=86400,
        queue_ttl_seconds=604800,
        governor=None,
        extra={"plugin_mode": "native"},
    )


@pytest.mark.asyncio
async def test_workflow_blocks_partial_activation(store):
    account = _account()
    scope = _scope()

    # 1. Missing credentials capability when required blocks workflow readiness
    creds_cfg = BusinessConfig(
        mode="supervised",
        required_policies=("content_policy",),
        operators=("operator-1",),
        rewrite_attempts=2,
        approval_ttl_seconds=86400,
        queue_ttl_seconds=604800,
        governor=None,
        extra={"plugin_mode": "native", "credentials_required": True},
    )
    svc_missing_creds = BusinessService(store, config_resolver=creds_cfg)
    missing_credentials_workflow = await check_workflow_readiness(
        svc_missing_creds,
        account,
        credentials_ready=False,
    )
    assert missing_credentials_workflow["ready"] is False
    assert "missing_credentials_capability" in missing_credentials_workflow["blockers"]

    # 2. Incomplete migration / cutover manifest blocks workflow readiness
    blocked_manifest = CutoverManifest(
        id="cman-blocked",
        source_digest="sha256:abc",
        account=account,
        stage="blocked",
        blockers=("active_legacy_writers:sales-ops-outreach",),
        native_generation=1,
    )
    svc_valid = BusinessService(store, config_resolver=_valid_config())
    incomplete_migration_workflow = await check_workflow_readiness(
        svc_valid,
        account,
        cutover_manifest=blocked_manifest,
    )
    assert incomplete_migration_workflow["ready"] is False
    assert any("incomplete_migration" in b or "active_legacy_writers" in b for b in incomplete_migration_workflow["blockers"])

    # 3. Missing operators or policy blocks readiness
    no_ops_cfg = BusinessConfig(
        mode="supervised",
        required_policies=("content_policy",),
        operators=(),
        rewrite_attempts=2,
        approval_ttl_seconds=86400,
        queue_ttl_seconds=604800,
        governor=None,
        extra={"plugin_mode": "native"},
    )
    svc_no_ops = BusinessService(store, config_resolver=no_ops_cfg)
    no_ops_workflow = await check_workflow_readiness(svc_no_ops, account)
    assert no_ops_workflow["ready"] is False
    assert "missing_operators" in no_ops_workflow["blockers"]

    no_pol_cfg = BusinessConfig(
        mode="supervised",
        required_policies=(),
        operators=("operator-1",),
        rewrite_attempts=2,
        approval_ttl_seconds=86400,
        queue_ttl_seconds=604800,
        governor=None,
        extra={"plugin_mode": "native"},
    )
    svc_no_pol = BusinessService(store, config_resolver=no_pol_cfg)
    no_pol_workflow = await check_workflow_readiness(svc_no_pol, account)
    assert no_pol_workflow["ready"] is False
    assert "missing_policy" in no_pol_workflow["blockers"]

    # 4. Non-native plugin mode blocks readiness
    legacy_plugin_cfg = BusinessConfig(
        mode="supervised",
        required_policies=("content_policy",),
        operators=("operator-1",),
        rewrite_attempts=2,
        approval_ttl_seconds=86400,
        queue_ttl_seconds=604800,
        governor=None,
        extra={"plugin_mode": "legacy"},
    )
    svc_legacy = BusinessService(store, config_resolver=legacy_plugin_cfg)
    legacy_workflow = await check_workflow_readiness(svc_legacy, account)
    assert legacy_workflow["ready"] is False
    assert any("plugin_mode_not_native" in b for b in legacy_workflow["blockers"])

    # 5. Competing legacy writers block readiness
    writers = ({"name": "sales-ops-queue", "pid": 9999, "status": "running"},)
    svc_competing = BusinessService(store, config_resolver=_valid_config())
    competing_workflow = await check_workflow_readiness(svc_competing, account, legacy_writers=writers)
    assert competing_workflow["ready"] is False
    assert any("competing_legacy_writers" in b for b in competing_workflow["blockers"])

    # 6. Full valid workflow readiness succeeds and contributes workflow_protocol=1
    ready_manifest = CutoverManifest(
        id="cman-ready",
        source_digest="sha256:abc",
        account=account,
        stage="ready",
        blockers=(),
        native_generation=1,
    )
    full_ready_workflow = await check_workflow_readiness(
        svc_valid,
        account,
        cutover_manifest=ready_manifest,
        credentials_ready=True,
    )
    assert full_ready_workflow["ready"] is True
    assert full_ready_workflow["workflow_protocol"] == 1
    assert full_ready_workflow["blockers"] == []

    # 7. Pilot report reflects supervised metrics without promoting mode or sending
    report = pilot_report(scope, store=store)
    assert report["ready"] is False
    assert report["auto_graduation_refused"] is True
    assert report["human_approval_required"] is True
    assert report["sends_emitted"] == 0
    assert report["mode_promoted"] is False
