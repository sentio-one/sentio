"""Push a review card to every configured operator when a draft is held."""
from __future__ import annotations

import logging

from gateway.business.models import Principal, Scope
from gateway.business.operator_notices import contact_label
from gateway.business.operators import _review_card, record_operator_card
from gateway.business.transport import send_operator_notice
from gateway.whatsapp_identity import to_whatsapp_jid

logger = logging.getLogger(__name__)


def _card_text(draft_text: str, public: dict, customer: str) -> str:
    return (
        f"Draft for {customer}:\n\n{draft_text}\n\n"
        f"React 👍 to this message to approve it, or send /messages reject {public['revision_id']}.\n"
        f"Revision {public['revision_id']}"
    )


async def push_review_cards(service, scope: Scope, config, revision_id: str) -> None:
    """Send one card per operator; a failure becomes an operator task, never a failed turn."""
    store = service.store
    principal = Principal("business:review-cards", scope.runtime_profile, frozenset({"digest"}), "service")
    revision = store.get_revision(scope, revision_id)
    plan = store.get_plan_by_revision(scope, revision_id)
    card = _review_card(service, principal, {"plan": plan})
    if card is None:
        return
    text = _card_text(revision.candidate.text or "", card["public"], contact_label(store, scope))
    for operator in config.operators:
        destination = to_whatsapp_jid(operator)
        notice_scope = Scope(scope.runtime_profile, scope.transport_profile, scope.transport_profile, destination)
        try:
            notice = await send_operator_notice(service, notice_scope, text)
        except Exception as exc:  # health: allow BLE001 -- a card failure must leave the held draft and raise a task
            logger.warning("review card to %s failed: %s", destination, exc, exc_info=True)
            notice = {"ok": False, "error": str(exc)}
        message_id = notice.get("message_id") or notice.get("messageId")
        if notice.get("ok") and message_id:
            record_operator_card(service, card, message_id)
        else:
            store.put_task(scope, dedupe_key=f"operator-notice:{revision_id}",
                           reason=str(notice.get("error") or "operator_notice_unavailable"), refs=(revision_id,))
