"""Operators get a human sentence after a decision, never the raw result object."""
import pytest

from gateway.business.operator_cards import push_review_cards
from gateway.business.operator_notices import approval_text, contact_label
from gateway.business.operators import handle_business_operator_reaction
from gateway.platforms.event import MessageEvent, MessageType
from gateway.session import SessionSource
from gateway.config import Platform
from tests.gateway.business.test_operator_card_push import OPERATOR, CUSTOMER, _consume, _held_draft, _service


def test_approved_text_names_the_customer_and_quotes_the_draft():
    text = approval_text({"ok": True, "approval_count": 1}, label="+27829533285", draft="Hi. How can I help?")
    assert text.startswith("✅ Approved for +27829533285")
    assert "Hi. How can I help?" in text
    assert "{" not in text and "approval_count" not in text


@pytest.mark.parametrize("code,fragment", [
    ("stale_revision", "out of date"),
    ("digest_mismatch", "changed"),
    ("unbound_digest", "no longer matches"),
    ("revision_not_found", "no longer exists"),
])
def test_refusals_say_why_in_words(code, fragment):
    text = approval_text({"error": code, "approval_count": 0}, label="+27829533285", draft=None)
    assert text.startswith("⚠️ Not approved") and fragment in text and "Nothing was sent" in text


def test_unknown_refusal_still_reads_as_a_sentence():
    text = approval_text({"error": "weird_code", "approval_count": 0}, label="x", draft=None)
    assert text.startswith("⚠️ Not approved") and "weird_code" in text


@pytest.mark.asyncio
async def test_thumbs_up_gets_a_human_confirmation_and_cards_name_the_number(tmp_path, monkeypatch):
    sent = []

    async def dispatch(request):
        await _consume(service, request)
        sent.append(request["part"]["payload"]["text"])
        return {"ok": True, "message_id": f"msg-{len(sent)}"}

    store, service = _service(tmp_path, dispatch)
    scope, rev = _held_draft(store)
    assert contact_label(store, scope) == "+27829533285"
    await push_review_cards(service, scope, service.resolve_config(scope), rev.id)
    assert "+27829533285" in sent[0] and scope.contact_id not in sent[0]

    runner = service.runner
    runner._business_service = service
    runner._is_user_authorized_for_source = lambda source: True
    operator_jid = f"{OPERATOR}@s.whatsapp.net"
    source = SessionSource(platform=Platform.WHATSAPP, chat_id=operator_jid, chat_type="dm", user_id=operator_jid)
    event = MessageEvent(text="[Reaction: 👍 to msg-1]", message_type=MessageType.TEXT, source=source,
                         metadata={"reaction_target_id": "msg-1", "reaction_emoji": "👍"})

    import time
    real = time.time
    monkeypatch.setattr("gateway.business.transport.time.time", lambda: real() + 60)  # clear the notice lane's pacing gap

    assert await handle_business_operator_reaction(runner, event) is True

    assert sent[1].startswith("✅ Approved for +27829533285")
    assert "Hi. How can I help?" in sent[1] and "{" not in sent[1]
