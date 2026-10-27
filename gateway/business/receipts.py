"""Delivery receipts, status joins, and business delivery ownership (Task B6)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hermes_cli.sqlite_util import transaction
from gateway.business.models import AccountKey, Receipt, Scope

if TYPE_CHECKING:
    from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)

STATUS_STATES = {
    0: "failed",
    1: "pending",
    2: "accepted",
    3: "delivered",
    4: "read",
    5: "played",
}

STATUS_NAMES = {
    0: "ERROR",
    1: "PENDING",
    2: "SERVER_ACK",
    3: "DELIVERY_ACK",
    4: "READ",
    5: "PLAYED",
}


@dataclass(frozen=True)
class DeliveryEvent:
    account: AccountKey
    message_id: str
    status: int
    status_name: str
    timestamp_ms: int


def owns_business_delivery(scope: Scope, source_id: str) -> bool:
    """Return True if the source_id or scope belongs to supervised business messaging."""
    if source_id.startswith(("biz-", "req-", "att-", "plan-", "outbox-")):
        return True
    return False


def apply_delivery_event(store: BusinessStore, event: DeliveryEvent) -> Receipt | None:
    """Apply an inbound delivery event, joining attempt state and aggregating part deliveries."""
    state = STATUS_STATES.get(event.status, "unknown")
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        # Record receipt idempotently
        db.execute(
            "INSERT INTO business_delivery_receipts(transport_profile, account_id, message_id, status, status_name, state, timestamp_ms) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(transport_profile, account_id, message_id) DO UPDATE SET "
            "status=excluded.status, status_name=excluded.status_name, state=excluded.state, timestamp_ms=excluded.timestamp_ms "
            "WHERE excluded.status > business_delivery_receipts.status OR (excluded.status=0 AND business_delivery_receipts.status<3)",
            (
                event.account.transport_profile,
                event.account.account_id,
                event.message_id,
                event.status,
                event.status_name,
                state,
                event.timestamp_ms,
            ),
        )

        matched_attempt = _find_attempt_by_message_id(db, event.account, event.message_id)
        if not matched_attempt:
            # Orphan receipt: retained early for subsequent join
            return Receipt(id=event.message_id, state=state, revision_id=None, reason=None)

        attempt_id = matched_attempt["attempt_id"]
        outbox_id = matched_attempt["outbox_id"]
        current_attempt_state = matched_attempt["state"]

        ranks = {"unknown": -1, "failed": 0, "pending": 1, "sent": 2, "accepted": 2, "delivered": 3, "read": 4, "played": 5}
        if (event.status != 0 or ranks.get(current_attempt_state, -1) >= 3) and ranks.get(current_attempt_state, -1) > ranks.get(state, -1):
            state = current_attempt_state

        db.execute("UPDATE business_attempts SET state=? WHERE attempt_id=?", (state, attempt_id))

        outbox_row = db.execute(
            "SELECT outbox_id, runtime_profile, transport_profile, account_id, contact_id, revision_id, plan_id "
            "FROM business_outbox WHERE outbox_id=?",
            (outbox_id,),
        ).fetchone()

        if not outbox_row:
            return Receipt(id=attempt_id, state=state, revision_id=None, reason=None)

        revision_id = outbox_row["revision_id"]
        _update_delivery_aggregates(db, outbox_row, state)
        return Receipt(id=attempt_id, state=state, revision_id=revision_id, reason=None)


def _find_attempt_by_message_id(db, account: AccountKey, message_id: str):
    rows = db.execute(
        "SELECT attempt_id, outbox_id, state, platform_ids FROM business_attempts "
        "WHERE transport_profile=? AND account_id=?",
        (account.transport_profile, account.account_id),
    ).fetchall()

    for r in rows:
        try:
            pids = json.loads(r["platform_ids"]) if r["platform_ids"] else []
            if message_id in pids:
                return r
        except (ValueError, TypeError):
            continue
    return None


def _update_delivery_aggregates(db, outbox_row, state: str) -> None:
    outbox_id = outbox_row["outbox_id"]
    scope_vals = (
        outbox_row["runtime_profile"],
        outbox_row["transport_profile"],
        outbox_row["account_id"],
        outbox_row["contact_id"],
    )
    revision_id = outbox_row["revision_id"]

    all_attempts = db.execute(
        "SELECT state FROM business_attempts WHERE outbox_id=?",
        (outbox_id,),
    ).fetchall()

    plan_row = db.execute(
        "SELECT payload FROM business_plans WHERE plan_id=?",
        (outbox_row["plan_id"],),
    ).fetchone()

    total_parts = 1
    if plan_row:
        try:
            pdict = json.loads(plan_row["payload"])
            total_parts = len(pdict.get("parts", [])) or 1
        except (ValueError, TypeError):
            total_parts = 1

    all_delivered = len(all_attempts) == total_parts and all(a["state"] in ("delivered", "read", "played") for a in all_attempts)

    rev_row = db.execute(
        "SELECT episode_id FROM business_revisions "
        "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
        (*scope_vals, revision_id),
    ).fetchone()
    episode_id = rev_row["episode_id"] if rev_row else None

    if all_delivered:
        db.execute("UPDATE business_outbox SET state='delivered' WHERE outbox_id=?", (outbox_id,))
        db.execute(
            "UPDATE business_revisions SET status='delivered' "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*scope_vals, revision_id),
        )
        if episode_id:
            db.execute(
                "UPDATE business_episodes SET state='delivered' "
                "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                (*scope_vals, episode_id),
            )
    elif state == "accepted" and episode_id:
        db.execute(
            "UPDATE business_episodes SET state='awaiting_delivery' "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
            (*scope_vals, episode_id),
        )

    from gateway.business.store_jobs import _refresh_outcomes
    _refresh_outcomes(db, outbox_row["runtime_profile"])


def join_attempt_receipts(store: BusinessStore, attempt_id: str) -> None:
    """Reconcile receipts retained before the transport response became durable."""
    with transaction(store._connect()) as db:
        row = db.execute("SELECT transport_profile,account_id,platform_ids FROM business_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if not row:
            return
        account = AccountKey(row["transport_profile"], row["account_id"])
        events = []
        for message_id in json.loads(row["platform_ids"]):
            receipt = db.execute("SELECT * FROM business_delivery_receipts WHERE transport_profile=? AND account_id=? AND message_id=?",
                                 (account.transport_profile, account.account_id, message_id)).fetchone()
            if receipt:
                events.append(DeliveryEvent(account, message_id, receipt["status"], receipt["status_name"], receipt["timestamp_ms"]))
    for event in events:
        apply_delivery_event(store, event)
