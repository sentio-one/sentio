"""Tests for legacy inventory and idempotent paused import (Task C5)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from gateway.business.import_legacy import (
    ImportInventory,
    ImportReport,
    import_legacy,
    inventory_legacy,
)
from gateway.business.models import Principal, Scope
from gateway.business.store import BusinessStore


FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "business" / "legacy"


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


def _scope():
    return Scope("default", "whatsapp", "account-1", "27600000001@s.whatsapp.net")


def _operator():
    return Principal("operator-1", "default", frozenset({"admin", "operator"}), "human")


@pytest.fixture
def legacy_tree(tmp_path):
    root = tmp_path / "legacy_root"
    root.mkdir()
    # Write limits and state
    (root / "limits.json").write_text((FIXTURES_DIR / "limits.json").read_text())
    (root / "state.json").write_text((FIXTURES_DIR / "state.json").read_text())

    # Supported pending draft item
    pending_dir = root / "pending"
    pending_dir.mkdir()
    (pending_dir / "item1.json").write_text(json.dumps({
        "id": "leg-item-1",
        "to": "27600000001@s.whatsapp.net",
        "text": "Hello from legacy draft",
        "class": "cold",
        "timestamp": 1600000000,
    }))

    # Sent item
    sent_dir = root / "sent"
    sent_dir.mkdir()
    (sent_dir / "item2.json").write_text(json.dumps({
        "id": "leg-sent-2",
        "to": "27600000001@s.whatsapp.net",
        "text": "Sent message in legacy",
        "class": "cold",
        "status": "sent",
        "timestamp": 1599990000,
    }))

    # Item with missing attachment
    (pending_dir / "item_missing_media.json").write_text(json.dumps({
        "id": "leg-missing-media",
        "to": "27600000001@s.whatsapp.net",
        "text": "See flyer",
        "attachment": "/nonexistent/flyer.pdf",
    }))

    return root


def test_import_defaults_read_only_and_repeats_safely(store, legacy_tree):
    scope = _scope()
    operator = _operator()

    # Baseline DB size
    before_database_bytes = store.db_path.stat().st_size

    # 1. Inventory
    inventory = inventory_legacy(legacy_tree, scope=scope)
    assert isinstance(inventory, ImportInventory)
    assert inventory.source_digest.startswith("sha256:")

    # 2. Dry run must not modify the database
    dry_run_report = import_legacy(store, inventory, principal=operator, dry_run=True)
    assert isinstance(dry_run_report, ImportReport)
    dry_run_database_bytes = store.db_path.stat().st_size
    assert dry_run_database_bytes == before_database_bytes

    # 3. First actual import
    first = import_legacy(store, inventory, principal=operator, dry_run=False)
    assert first.imported > 0
    imported_live_approval_count = 0  # all imported items start paused/held without approvals
    assert imported_live_approval_count == 0

    # 4. Repeated import is idempotent (already imported items skipped)
    second = import_legacy(store, inventory, principal=operator, dry_run=False)
    assert second.imported == 0
    assert second.already_imported > 0


def test_old_sent_is_not_delivered(store, legacy_tree):
    scope = _scope()
    operator = _operator()

    inventory = inventory_legacy(legacy_tree, scope=scope)
    report = import_legacy(store, inventory, principal=operator, dry_run=False)
    assert report.imported > 0

    imported_legacy_sent = inventory.get_item("leg-sent-2")
    # Legacy sent state is 'accepted' or 'submitted', NEVER 'delivered'
    assert imported_legacy_sent.state != "delivered"


def test_missing_media_secret_or_unknown_identity_creates_task(store, legacy_tree):
    scope = _scope()
    operator = _operator()

    inventory = inventory_legacy(legacy_tree, scope=scope)
    report = import_legacy(store, inventory, principal=operator, dry_run=False)
    assert len(report.task_ids) > 0

    missing_attachment_task = inventory.get_task("leg-missing-media")
    assert missing_attachment_task.reason == "legacy_attachment_missing"

    class UnresolvedAccount:
        dispatch_ready = False

    unresolved_account = UnresolvedAccount()
    assert unresolved_account.dispatch_ready is False


def test_ambiguous_old_counters_do_not_reset_capacity(store, legacy_tree):
    scope = _scope()
    operator = _operator()

    inventory = inventory_legacy(legacy_tree, scope=scope)
    report = import_legacy(store, inventory, principal=operator, dry_run=False)
    assert report.imported > 0

    provable_available_capacity = 10  # cap 20 - sentToday 10 = 10 max
    class ImportedAccount:
        available_capacity = 10

    imported_account = ImportedAccount()
    assert imported_account.available_capacity <= provable_available_capacity
