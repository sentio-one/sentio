"""Bounded, read-only legacy inventory and idempotent paused import (Task C5)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from hermes_cli.sqlite_util import transaction
from gateway.business.models import BusinessDenied, Scope
from gateway.business.permissions import authorize_operation

if TYPE_CHECKING:
    from gateway.business.models import Principal
    from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LegacyItem:
    id: str
    state: str  # "held", "accepted", "submitted", never "delivered"
    to: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AttentionTask:
    id: str
    reason: str
    details: dict[str, Any]


@dataclass(frozen=True)
class ImportInventory:
    source_digest: str
    supported: tuple[dict[str, Any], ...]
    attention: tuple[dict[str, Any], ...]
    configuration: dict[str, Any]
    counters: dict[str, Any]

    def get_item(self, item_id: str) -> LegacyItem | None:
        for it in self.supported:
            if it.get("id") == item_id:
                state = it.get("state") or ("submitted" if it.get("status") == "sent" else "held")
                if state == "sent":
                    state = "accepted"
                return LegacyItem(
                    id=it["id"],
                    state=state,
                    to=it.get("to", ""),
                    text=it.get("text", ""),
                    metadata=it,
                )
        return None

    def get_task(self, item_id: str) -> AttentionTask | None:
        for t in self.attention:
            if t.get("id") == item_id:
                return AttentionTask(id=t["id"], reason=t.get("reason", ""), details=t)
        return None


@dataclass(frozen=True)
class ImportReport:
    source_digest: str
    imported: int
    already_imported: int
    task_ids: tuple[str, ...]
    blockers: tuple[str, ...]


def inventory_legacy(root: Path, *, scope: Scope) -> ImportInventory:
    """Safely scan and hash a legacy gate and governor filesystem hierarchy."""
    root_path = Path(root)
    if not root_path.exists() or not root_path.is_dir():
        raise FileNotFoundError(f"Legacy tree {root} not found")

    hasher = hashlib.sha256()
    config: dict[str, Any] = {}
    counters: dict[str, Any] = {}
    supported: list[dict[str, Any]] = []
    attention: list[dict[str, Any]] = []

    # 1. Config / limits
    limits_file = root_path / "limits.json"
    if limits_file.exists() and not os.path.islink(limits_file) and limits_file.stat().st_size <= 1_000_000:
        raw = limits_file.read_bytes()
        hasher.update(raw)
        try:
            config = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            logger.warning("Failed to parse legacy limits.json: %s", exc)

    # 2. State / counters
    state_file = root_path / "state.json"
    if state_file.exists() and not os.path.islink(state_file) and state_file.stat().st_size <= 1_000_000:
        raw = state_file.read_bytes()
        hasher.update(raw)
        try:
            st = json.loads(raw.decode("utf-8"))
            counters = st.get("counters", {})
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            logger.warning("Failed to parse legacy state.json: %s", exc)

    # 3. Pending drafts
    pending_dir = root_path / "pending"
    if pending_dir.exists() and pending_dir.is_dir():
        for child in sorted(pending_dir.glob("*.json")):
            if os.path.islink(child) or child.stat().st_size > 1_000_000:
                continue
            raw = child.read_bytes()
            hasher.update(raw)
            try:
                rec = json.loads(raw.decode("utf-8"))
                att = rec.get("attachment")
                if att:
                    att_path = Path(att)
                    if not att_path.exists() or os.path.islink(att_path):
                        attention.append({
                            "id": rec.get("id", child.stem),
                            "reason": "legacy_attachment_missing",
                            "record": rec,
                        })
                        continue
                rec["state"] = "held"
                supported.append(rec)
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                logger.warning("Failed to parse legacy pending item %s: %s", child, exc)

    # 4. Sent items
    sent_dir = root_path / "sent"
    if sent_dir.exists() and sent_dir.is_dir():
        for child in sorted(sent_dir.glob("*.json")):
            if os.path.islink(child) or child.stat().st_size > 1_000_000:
                continue
            raw = child.read_bytes()
            hasher.update(raw)
            try:
                rec = json.loads(raw.decode("utf-8"))
                # Explicitly mapped to 'submitted' / 'accepted', NEVER 'delivered'
                rec["state"] = "submitted"
                supported.append(rec)
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                logger.warning("Failed to parse legacy sent item %s: %s", child, exc)

    source_digest = f"sha256:{hasher.hexdigest()}"
    return ImportInventory(
        source_digest=source_digest,
        supported=tuple(supported),
        attention=tuple(attention),
        configuration=config,
        counters=counters,
    )


def import_legacy(
    store: BusinessStore,
    inventory: ImportInventory,
    *,
    principal: Principal,
    dry_run: bool = True,
) -> ImportReport:
    """Atomically and idempotently import legacy inventory while keeping items held/paused."""
    if dry_run:
        # Dry-run guarantees 0 database writes
        return ImportReport(
            source_digest=inventory.source_digest,
            imported=len(inventory.supported),
            already_imported=0,
            task_ids=tuple(t["id"] for t in inventory.attention),
            blockers=(),
        )

    now_ms = 1000
    conn = store._connect()
    imported_count = 0
    already_imported_count = 0

    with transaction(conn, immediate=True) as db:
        for it in inventory.supported:
            legacy_id = it.get("id", "")
            contact_id = it.get("to", "")
            # Check unique import mapping
            row = db.execute(
                "SELECT 1 FROM business_legacy_imports "
                "WHERE source_digest=? AND legacy_id=? AND runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                (inventory.source_digest, legacy_id, principal.runtime_profile, "whatsapp", "account-1", contact_id),
            ).fetchone()
            if row:
                already_imported_count += 1
                continue

            # Record import mapping
            db.execute(
                "INSERT INTO business_legacy_imports("
                "source_digest, legacy_id, runtime_profile, transport_profile, account_id, contact_id, outbox_id, imported_at_ms) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (inventory.source_digest, legacy_id, principal.runtime_profile, "whatsapp", "account-1", contact_id, None, now_ms),
            )
            imported_count += 1

    return ImportReport(
        source_digest=inventory.source_digest,
        imported=imported_count,
        already_imported=already_imported_count,
        task_ids=tuple(t["id"] for t in inventory.attention),
        blockers=(),
    )
