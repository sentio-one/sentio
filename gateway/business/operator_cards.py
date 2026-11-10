"""Push a review card to every configured operator when a draft is held."""
from __future__ import annotations

import logging
import re

from gateway.business.models import Principal, Scope
from gateway.business.operator_notices import contact_label, customer_said, review_card_text, silence_text
from gateway.business.operators import _review_card, record_operator_card
from gateway.business.transport import send_operator_notice
from gateway.whatsapp_identity import to_whatsapp_jid

logger = logging.getLogger(__name__)


async def push_review_cards(service, scope: Scope, config, revision_id: str) -> None:
    """Send one card per operator; a failure becomes an operator task, never a failed turn."""
    store = service.store
    principal = Principal("business:review-cards", scope.runtime_profile, frozenset({"digest"}), "service")
    revision = store.get_revision(scope, revision_id)
    plan = store.get_plan_by_revision(scope, revision_id)
    card = _review_card(service, principal, {"plan": plan})
    if card is None:
        return
    text = review_card_text(code=revision_id[:6], label=contact_label(store, scope), said=customer_said(store, scope),
                            draft=revision.candidate.text or "")
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


async def push_silence_notice(service, scope: Scope, config, reason: str) -> None:
    """Tell operators the agent chose not to reply, so silence is never invisible to someone who cannot open the chat."""
    store = service.store
    said = customer_said(store, scope)
    if not any(re.search(r"\w", text) for text in said):
        return  # a bare reaction or emoji needs no explanation
    text = silence_text(label=contact_label(store, scope), said=said, reason=reason)
    for operator in config.operators:
        notice_scope = Scope(scope.runtime_profile, scope.transport_profile, scope.transport_profile, to_whatsapp_jid(operator))
        try:
            await send_operator_notice(service, notice_scope, text)
        except Exception as exc:  # health: allow BLE001 -- an alert must never fail the customer's turn
            logger.warning("no-reply notice to %s failed: %s", notice_scope.contact_id, exc, exc_info=True)
