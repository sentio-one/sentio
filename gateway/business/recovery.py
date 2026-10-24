"""Single-owner crash recovery and outcome reconciliation (Task B6)."""

from __future__ import annotations

import logging
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
    with transaction(conn, immediate=True) as db:
        journal_map = {j["attempt_id"]: j for j in journal if "attempt_id" in j}

        rows = db.execute(
            "SELECT a.attempt_id, a.outbox_id, a.state, a.platform_ids, o.revision_id "
            "FROM business_attempts a "
            "JOIN business_outbox o ON a.outbox_id = o.outbox_id "
            "WHERE a.transport_profile=? AND a.account_id=?",
            (account.transport_profile, account.account_id),
        ).fetchall()

        for row in rows:
            att_id = row["attempt_id"]
            state = row["state"]
            rev_id = row["revision_id"]

            j_entry = journal_map.get(att_id)
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

    return tuple(reconciled)
