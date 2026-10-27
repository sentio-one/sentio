"""Transactional account pacing, sending windows, and capacity governance (Task B3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.business.models import AccountKey, Principal
    from gateway.business.store import BusinessStore


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    not_before_ms: int | None = None
    reason: str | None = None


def eligibility(
    store: BusinessStore,
    account: AccountKey,
    *,
    message_class: str,
    now_ms: int,
    config: dict[str, Any] | None = None,
) -> Eligibility:
    """Evaluate account eligibility against windows, gaps, and capacity caps."""
    return store.governor_eligibility(
        account,
        message_class=message_class,
        now_ms=now_ms,
        config=config,
    )


def reserve_attempt(
    store: BusinessStore,
    account: AccountKey,
    attempt_id: str,
    *,
    message_class: str,
    now_ms: int,
    config: dict[str, Any] | None = None,
) -> Eligibility:
    """Atomically check eligibility and reserve physical sending capacity."""
    return store.governor_reserve_attempt(
        account,
        attempt_id=attempt_id,
        message_class=message_class,
        now_ms=now_ms,
        config=config,
    )


def complete_attempt(
    store: BusinessStore,
    account: AccountKey,
    attempt_id: str,
    *,
    submitted: bool,
    finished_at_ms: int,
) -> None:
    """Release in-flight capacity and record attempt outcome."""
    store.governor_complete_attempt(
        account,
        attempt_id=attempt_id,
        submitted=submitted,
        finished_at_ms=finished_at_ms,
    )


def pause_account(
    store: BusinessStore,
    principal: Principal,
    account: AccountKey,
    *,
    reason: str,
    now_ms: int,
) -> None:
    """Pause account outbound sending immediately."""
    store.governor_pause_account(principal, account, reason=reason, now_ms=now_ms)


def resume_account(
    store: BusinessStore,
    principal: Principal,
    account: AccountKey,
    *,
    now_ms: int,
) -> None:
    """Resume a paused account."""
    store.governor_resume_account(principal, account, now_ms=now_ms)
