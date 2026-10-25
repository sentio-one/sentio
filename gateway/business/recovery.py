"""Single-owner crash recovery and outcome reconciliation (Task B6)."""

from __future__ import annotations

import logging
import json
import time
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from hermes_cli.sqlite_util import transaction
from gateway.business.models import AccountKey, Receipt

if TYPE_CHECKING:
    from gateway.business.service import BusinessService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryReceipt(Receipt):
    automatic_retry_count: int = 0


async def reconcile_account(
    service: BusinessService,
    account: AccountKey,
    *,
    journal: tuple[dict[str, Any], ...] = (),
) -> tuple[Receipt, ...]:
    """Reconcile in-flight attempts against fsynced journal across crash boundaries without duplicated sends."""
    reconciled: list[Receipt] = []
    store = service.store
    conn = store._connect()
    governor_finalization: dict[str, bool] = {}
    with transaction(conn, immediate=True) as db:
        journal_map = {j["attempt_id"]: j for j in journal if "attempt_id" in j}

        rows = db.execute(
            "SELECT a.attempt_id, a.outbox_id, a.state, a.platform_ids, o.revision_id, "
            "EXISTS(SELECT 1 FROM business_governor_reservations g WHERE g.attempt_id=a.attempt_id) AS has_reservation "
            "FROM business_attempts a "
            "JOIN business_outbox o ON a.outbox_id = o.outbox_id "
            "WHERE a.transport_profile=? AND a.account_id=?",
            (account.transport_profile, account.account_id),
        ).fetchall()

        joined = []
        for row in rows:
            att_id = row["attempt_id"]
            state = row["state"]
            rev_id = row["revision_id"]

            # Recovery is a transition for unresolved work, never a replay of
            # already finalized attempts. Replaying successful journal rows
            # would duplicate governor history on every restart.
            if state in ("sent", "accepted", "delivered", "read", "played", "failed"):
                if row["has_reservation"]:
                    governor_finalization[att_id] = state != "failed"
                if state != "failed":
                    # A crash can land after recording the socket result but
                    # before joining an early delivery receipt.
                    joined.append(att_id)
                continue
            if state not in ("claimed", "in_flight", "unknown"):
                continue

            j_entry = journal_map.get(att_id)
            if state == "unknown" and not (
                j_entry and j_entry.get("event") == "outcome" and j_entry.get("success") and j_entry.get("message_id")
            ):
                continue
            if j_entry and j_entry.get("event") == "outcome" and j_entry.get("success") and j_entry.get("message_id"):
                db.execute("UPDATE business_attempts SET state='sent', platform_ids=?, error_code=NULL WHERE attempt_id=?",
                           (json.dumps([j_entry["message_id"]]), att_id))
                joined.append(att_id)
                if row["has_reservation"]:
                    governor_finalization[att_id] = True
                reconciled.append(RecoveryReceipt(id=att_id,state="sent",revision_id=rev_id,reason="journal_reconciled"))
                continue
            if state in ("claimed", "in_flight") or (j_entry and j_entry.get("status") in ("claimed", "in_flight")):
                db.execute(
                    "UPDATE business_attempts SET state='unknown', error_code='unreconciled_crash' WHERE attempt_id=?",
                    (att_id,),
                )
                rec = RecoveryReceipt(
                    id=att_id,
                    state="unknown",
                    revision_id=rev_id,
                    reason="crash_in_flight",
                    automatic_retry_count=0,
                )
                reconciled.append(rec)
                if row["has_reservation"]:
                    governor_finalization[att_id] = True

    from gateway.business.receipts import join_attempt_receipts
    for attempt_id, submitted in governor_finalization.items():
        store.governor_complete_attempt(account,attempt_id,submitted=submitted,finished_at_ms=int(time.time()*1000))
    for receipt in reconciled:
        context = store.get_attempt_context(receipt.id)
        if receipt.state == "unknown" and context:
            from gateway.business.models import Scope
            scope = Scope(**{k: context[k] for k in ("runtime_profile", "transport_profile", "account_id", "contact_id")})
            store.put_task(scope, dedupe_key=f"attempt:{receipt.id}", reason="unknown_delivery", refs=(receipt.id,))
    for attempt_id in joined:
        join_attempt_receipts(store, attempt_id)
    return tuple(reconciled)
