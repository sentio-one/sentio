"""Exact-revision authorization and immutable approval records (Task B2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.business.models import Principal, Scope
    from gateway.business.store import BusinessStore


@dataclass(frozen=True)
class Approval:
    id: str
    plan_id: str
    revision_id: str
    actor: str
    approved_at_ms: int
    expires_at_ms: int
    override_reason: str | None = None


async def approve_revision(
    store: BusinessStore,
    principal: Principal,
    scope: Scope,
    *,
    revision_id: str,
    plan_digest: str,
    now_ms: int,
    override_reason: str | None = None,
) -> Approval:
    """Authorize an exact draft revision bound to its rendered plan digest."""
    return store.approve_revision(
        principal,
        scope,
        revision_id=revision_id,
        plan_digest=plan_digest,
        now_ms=now_ms,
        override_reason=override_reason,
    )
