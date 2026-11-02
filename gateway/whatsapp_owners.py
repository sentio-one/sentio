"""Negative admission gate for the primary profile of a shared WhatsApp bot.

Routed senders and secondary bots keep ordinary authorization. Owners also need
ordinary authorization; this gate never grants access.
"""
from __future__ import annotations

from typing import Any

_WHATSAPP_PLATFORMS = frozenset({"whatsapp", "whatsapp_cloud"})


def parse_owners(raw: Any) -> list[str]:
    """Parse list-valued owner configuration, discarding empty entries."""
    if not isinstance(raw, list):
        return []
    return [text for value in raw if value is not None and (text := str(value).strip())]


def _aliases(identifier: str) -> set[str]:
    from gateway.whatsapp_identity import expand_whatsapp_aliases

    identifier = str(identifier).strip()
    # Group/channel identifiers can share a numeric prefix with a person.
    if "@" in identifier and identifier.rsplit("@", 1)[1] not in {"s.whatsapp.net", "lid"}:
        return set()
    return expand_whatsapp_aliases(identifier)


def is_owner(owners: list[str], user_id: str | None) -> bool:
    """Match phone, device-JID and mapped-LID aliases of a person."""
    sender = _aliases(user_id) if user_id else set()
    return bool(sender) and any(sender & _aliases(owner) for owner in owners)


def owner_gate(
    owners: list[str], *, platform: str, user_id: str | None, identity: Any, primary_profile: str,
) -> bool | None:
    """Refuse an unrouted primary-bot non-owner; otherwise defer to normal admission."""
    if not owners or platform not in _WHATSAPP_PLATFORMS or identity is None:
        return None
    if identity.transport_profile != primary_profile or identity.runtime_profile != primary_profile:
        return None
    return None if is_owner(owners, user_id) else False
