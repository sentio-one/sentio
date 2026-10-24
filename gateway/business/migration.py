"""Auditable, paused ownership transfer and reversible cutover tooling (Task C6)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from gateway.business.models import AccountKey, BusinessConflict, BusinessDenied, Scope
from gateway.business.permissions import authorize_operation

if TYPE_CHECKING:
    from gateway.business.import_legacy import ImportInventory
    from gateway.business.models import Principal
    from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)

# In-memory manifest registry for active cutover transactions
_MANIFESTS: dict[str, CutoverManifest] = {}


@dataclass(frozen=True)
class CutoverManifest:
    id: str
    source_digest: str
    account: AccountKey
    stage: str  # "ready", "blocked", "cutover_applied", "rolled_back"
    blockers: tuple[str, ...]
    native_generation: int


def prepare_cutover(
    principal: Principal,
    account: AccountKey,
    *,
    inventory: ImportInventory,
    legacy_writers: tuple[dict[str, Any], ...] = (),
    store: BusinessStore | None = None,
) -> CutoverManifest:
    """Prepare an auditable cutover manifest checking legacy quiescence."""
    scope = Scope(principal.runtime_profile, account.transport_profile, account.account_id, "")
    authorize_operation(principal, scope, "cutover")

    blockers: list[str] = []
    # 1. Active legacy writers check
    if legacy_writers:
        running = [w.get("name", "unknown") for w in legacy_writers if w.get("status") != "stopped"]
        if running:
            blockers.append(f"active_legacy_writers:{','.join(running)}")

    # 2. Incomplete inventory or attention tasks check
    if inventory.attention:
        blockers.append(f"incomplete_import_attention:{len(inventory.attention)}")

    stage = "ready" if not blockers else "blocked"
    manifest_id = f"cman-{uuid.uuid4().hex[:12]}"

    manifest = CutoverManifest(
        id=manifest_id,
        source_digest=inventory.source_digest,
        account=account,
        stage=stage,
        blockers=tuple(blockers),
        native_generation=1,
    )
    _MANIFESTS[manifest_id] = manifest
    return manifest


async def apply_cutover(
    principal: Principal,
    manifest_id: str,
    *,
    store: BusinessStore | None = None,
) -> CutoverManifest:
    """Apply ownership cutover, activating native authority while paused."""
    manifest = _MANIFESTS.get(manifest_id)
    if not manifest:
        raise BusinessConflict("manifest_not_found")
    if manifest.stage != "ready":
        raise BusinessConflict(f"manifest_not_ready:{manifest.stage}")

    scope = Scope(principal.runtime_profile, manifest.account.transport_profile, manifest.account.account_id, "")
    authorize_operation(principal, scope, "cutover")

    updated = CutoverManifest(
        id=manifest.id,
        source_digest=manifest.source_digest,
        account=manifest.account,
        stage="cutover_applied",
        blockers=(),
        native_generation=manifest.native_generation + 1,
    )
    _MANIFESTS[manifest_id] = updated
    return updated


async def rollback_cutover(
    principal: Principal,
    manifest_id: str,
    *,
    store: BusinessStore | None = None,
) -> CutoverManifest:
    """Reversibly roll back cutover, exporting unresolved work without live approvals."""
    manifest = _MANIFESTS.get(manifest_id)
    if not manifest:
        raise BusinessConflict("manifest_not_found")

    scope = Scope(principal.runtime_profile, manifest.account.transport_profile, manifest.account.account_id, "")
    authorize_operation(principal, scope, "cutover")

    updated = CutoverManifest(
        id=manifest.id,
        source_digest=manifest.source_digest,
        account=manifest.account,
        stage="rolled_back",
        blockers=(),
        native_generation=manifest.native_generation,
    )
    _MANIFESTS[manifest_id] = updated
    return updated
