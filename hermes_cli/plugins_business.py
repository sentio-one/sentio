"""Business-only access to callbacks through the plugin ownership ledger."""

from __future__ import annotations

from typing import Any


def callbacks_for_required_policy(plugin_id: str, hook: str) -> tuple[Any, ...]:
    """Return live callback refs whose ledger owner exactly matches the required plugin id."""
    from hermes_cli.plugins import get_plugin_manager

    manager = get_plugin_manager()
    return tuple(
        registration.callback
        for owner, registrations in manager._ownership_ledger.items()
        if owner == plugin_id
        for registration in registrations
        if registration.active and registration.kind == "hook" and registration.key == hook
        and registration.callback is not None
    )
