"""Promotion of checked customer drafts into the authoritative held outbox."""

import time
from pathlib import Path

from gateway.business.models import Receipt
from gateway.business.render import render_delivery_plan


async def promote_draft(store, scope, receipt: Receipt, config, *, now_ms=None, request_id=None) -> Receipt:
    if receipt.state != "held" or not receipt.revision_id:
        return receipt
    revision = store.get_revision(scope, receipt.revision_id)
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    plan = store.get_plan_by_revision(scope, revision.id)
    if plan is None:
        plan = await render_delivery_plan(
            scope, revision, now_ms=now_ms,
            capture_dir=Path(store.db_path).parent / "business" / "attachments",
            default_ttl_ms=min(config.approval_ttl_seconds, config.queue_ttl_seconds) * 1000,
        )
        store.save_plan(plan)
    return store.submit_revision(scope, revision.id, request_id=request_id or f"draft:{revision.id}", now_ms=now_ms)
