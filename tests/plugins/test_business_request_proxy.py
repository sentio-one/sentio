"""Tests for scoped PluginContext.business_request proxy (Task C1)."""

from __future__ import annotations

import pytest

from gateway.business.models import Principal, Scope
from hermes_cli.plugins_business_client import handle_plugin_business_request


def test_plugin_scope_cannot_approve_or_read_foreign_queue():
    plugin_principal = Principal("plugin:whatsapp-sales-ops", "default", frozenset({"plugin"}), "plugin")
    scope = Scope("default", "whatsapp", "account-1", "27600000001@s.whatsapp.net")

    # Plugin attempts to approve a revision directly
    plugin_approve_result = handle_plugin_business_request(
        plugin_principal,
        scope,
        operation="approve",
        params={"revision_id": "rev-1"},
    )
    assert plugin_approve_result["error"] == "permission_denied"

    # Plugin attempts to change business mode
    plugin_mode_result = handle_plugin_business_request(
        plugin_principal,
        scope,
        operation="set_mode",
        params={"mode": "supervised"},
    )
    assert plugin_mode_result["error"] == "permission_denied"

    # Permitted operations execute cleanly
    permitted_status = handle_plugin_business_request(
        plugin_principal,
        scope,
        operation="status",
        params={},
    )
    assert permitted_status.get("error") is None
    assert "status" in permitted_status
