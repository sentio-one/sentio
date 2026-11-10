"""Short typed review commands from a verified operator: ``ok``, ``no`` and ``edit`` plus a draft's code."""
from __future__ import annotations

import re

from gateway.business.models import Scope

_COMMAND = re.compile(r"^\s*(edit|ok|yes|send|approve|no|reject|discard)\s+([0-9a-f]{6})(?![0-9a-z])[\s:,-]*(.*)$",
                      re.IGNORECASE | re.DOTALL)
_KIND = {"edit": "edit", "ok": "approve", "yes": "approve", "send": "approve", "approve": "approve",
         "no": "reject", "reject": "reject", "discard": "reject"}


def parse_shorthand(text: str) -> tuple[str, str, str] | None:
    """(kind, code, rest) for a review command, None for anything else so ordinary chat is untouched."""
    match = _COMMAND.match(text or "")
    if not match:
        return None
    return _KIND[match.group(1).lower()], match.group(2).lower(), match.group(3).strip()


async def _notice(op, text: str, key: str) -> None:
    from gateway.business.transport import send_operator_notice
    notice = await send_operator_notice(op.service, op.notice_scope, text)
    if not notice.get("ok"):
        op.service.store.put_task(op.notice_scope, dedupe_key=f"operator-reaction:{key}",
                                  reason="operator_notice_unavailable", refs=(key,))


def _customer(row) -> Scope:
    return Scope(*(row[key] for key in ("runtime_profile", "transport_profile", "account_id", "contact_id")))


def _scope_dict(row) -> dict:
    return {key: row[key] for key in ("runtime_profile", "transport_profile", "account_id", "contact_id")}


async def approve_card(op, row, *, action: str, key: str) -> None:
    from gateway.business.operator_notices import approval_text, contact_label, draft_text
    from gateway.business.operators import handle_operator_event
    result = await handle_operator_event(op.service, {
        "action": action, "digest_id": row["digest_id"], "revision_id": row["revision_id"],
        "plan_digest": row["plan_digest"], "target_message_id": row["message_id"], "scope": _scope_dict(row)}, principal=op.principal)
    customer = _customer(row)
    await _notice(op, approval_text(result, label=contact_label(op.service.store, customer),
                                    draft=draft_text(op.service.store, customer, row["revision_id"])), key)


async def reject_card(op, row, reason: str, *, key: str) -> None:
    from gateway.business.control import execute_business_request
    from gateway.business.operator_notices import contact_label, rejection_text
    result = await execute_business_request(op.service, operation="operator.reject", principal=op.principal,
                                            params={"revision_id": row["revision_id"], "reason": reason or "operator_rejected"})
    await _notice(op, rejection_text(result, label=contact_label(op.service.store, _customer(row))), key)


async def edit_card(op, row, text: str, *, key: str) -> None:
    from gateway.business.control import execute_business_request
    from gateway.business.operator_notices import contact_label, edit_text
    from gateway.business.operators import _review_card, handle_operator_event
    label = contact_label(op.service.store, _customer(row))
    result = await execute_business_request(op.service, operation="operator.edit", principal=op.principal,
                                            params={"revision_id": row["revision_id"], "text": text})
    card = _review_card(op.service, op.principal, result) if result.get("ok") and result.get("plan") else None
    if card is None:
        failed = result if result.get("state") == "attention" or not result.get("ok") else {**result, "error": "edit_failed"}
        await _notice(op, edit_text(failed, label=label, text=text), key)
        return
    approval = await handle_operator_event(op.service, {
        "action": "shorthand", "digest_id": card["digest_id"], "revision_id": card["revision_id"],
        "plan_digest": card["plan_digest"], "target_message_id": row["message_id"], "scope": _scope_dict(row)}, principal=op.principal)
    await _notice(op, edit_text(approval, label=label, text=text), key)


def _card_for_code(op, code: str):
    with op.service.store._connect() as db:
        rows = db.execute("SELECT * FROM business_operator_cards WHERE runtime_profile=? AND transport_profile=? "
                          "AND account_id=? AND revision_id LIKE ? ORDER BY created_at_ms DESC",
                          (op.runtime, op.transport, op.transport, f"{code}%")).fetchall()
    revisions = {row["revision_id"] for row in rows}
    return rows[0] if len(revisions) == 1 else None


async def handle_business_operator_shorthand(runner, event) -> bool:
    """True when the message was a review command from a verified operator; anything else is not ours."""
    command = parse_shorthand(getattr(event, "text", "") or "")
    if command is None:
        return False
    from gateway.business.operators import verified_operator
    op = verified_operator(runner, event)
    if op is None:
        return False
    kind, code, rest = command
    key = str(event.message_id or code)
    row = _card_for_code(op, code)
    if row is None:
        await _notice(op, f"⚠️ I don't have a draft with the code {code}. Use the code on the draft's card.", key)
    elif kind == "approve":
        await approve_card(op, row, action="shorthand", key=key)
    elif kind == "reject":
        await reject_card(op, row, rest, key=key)
    elif not rest:
        await _notice(op, f"Put your text after the code, e.g. edit {code} Hi, yes we can help with that.", key)
    else:
        await edit_card(op, row, rest, key=key)
    return True
