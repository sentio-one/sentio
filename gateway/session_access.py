"""Profile grants on an authority connection (``allowed_profiles`` in the identity).

A dashboard identity minted under an access policy carries ``allowed_profiles``: the only served
profiles its connection may route to or run on. An identity without the key keeps the native
ticket's ``profile_scope`` semantics. The names compared are canonical profile ids, resolved from
the authority actually reached, never from the caller's selector spelling.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from hermes_state_runtime import RuntimeStoreError

logger = logging.getLogger(__name__)


def allowed_set(identity: dict) -> Optional[frozenset]:
    """The caller's granted profile names, or None when the identity is not grant-scoped."""
    raw = identity.get('allowed_profiles')
    if raw is None:
        return None
    return frozenset(str(name) for name in raw) if isinstance(raw, (list, tuple, set, frozenset)) else frozenset()


def authority_profile_name(authority: Any) -> str:
    from hermes_constants import profile_name_for_home

    return profile_name_for_home(authority.profile_id) or 'default'


def require_granted(allowed: Optional[frozenset], authority: Any, *, subject: str, requested: Any, method: Any) -> None:
    """Refuse (``profile_mismatch``, the same reason an unserved profile gets, so a denial reveals
    nothing about which profiles exist) unless *authority*'s profile is granted."""
    if allowed is None or authority_profile_name(authority) in allowed:
        return
    logger.warning('profile access denied: subject=%s requested=%.64r method=%s', subject, requested, method)
    raise RuntimeStoreError('profile_mismatch')
