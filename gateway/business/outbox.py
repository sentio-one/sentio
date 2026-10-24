"""Durable, stale-safe outbox scheduling and attempt claims (Task B2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.business.models import AccountKey, Principal, Receipt, Scope
    from gateway.business.store import BusinessStore


@dataclass(frozen=True)
class Attempt:
    id: str
    outbox_id: str
    part_index: int
    generation: int
    state: str
    platform_ids: tuple[str, ...]
    error_code: str | None = None


async def submit_revision(
    store: BusinessStore,
    scope: Scope,
    revision_id: str,
    *,
    request_id: str,
    now_ms: int,
) -> Receipt:
    """Submit a revision to the outbox in 'held' state."""
    return store.submit_revision(
        scope,
        revision_id=revision_id,
        request_id=request_id,
        now_ms=now_ms,
    )


async def reject_revision(
    store: BusinessStore,
    principal: Principal,
    scope: Scope,
    *,
    revision_id: str,
    reason: str,
    now_ms: int,
) -> Receipt:
    """Reject a draft revision and cancel any pending outbox items."""
    return store.reject_revision(
        principal,
        scope,
        revision_id=revision_id,
        reason=reason,
        now_ms=now_ms,
    )


async def claim_part(
    store: BusinessStore,
    account: AccountKey,
    *,
    now_ms: int,
) -> Attempt | None:
    """Atomically claim the next eligible approved delivery part for an account."""
    return store.claim_part(account, now_ms=now_ms)


async def invalidate_contact(
    store: BusinessStore,
    scope: Scope,
    *,
    reason: str,
    now_ms: int,
) -> int:
    """Invalidate all pending outbox entries and approvals for a contact."""
    return store.invalidate_contact(scope, reason=reason, now_ms=now_ms)
