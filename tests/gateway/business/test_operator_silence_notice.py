"""When the agent decides not to reply, the operator is told why instead of seeing silence."""
import pytest

from gateway.business.models import ContextEvent
from gateway.business.operator_cards import push_silence_notice
from tests.gateway.business.test_operator_card_push import CUSTOMER, _consume, _service


def _heard(store, text):
    scope = store.bind_contact("default", "default", "default", [CUSTOMER])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", text, None, "inbound"), source_id="s1")
    return scope


@pytest.mark.asyncio
async def test_operator_is_told_what_the_lead_said_and_why_there_is_no_reply(tmp_path):
    sent = []

    async def dispatch(request):
        await _consume(service, request)
        sent.append(request["part"]["payload"]["text"])
        return {"ok": True, "message_id": "n1"}

    store, service = _service(tmp_path, dispatch)
    scope = _heard(store, "yes please")
    await push_silence_notice(service, scope, service.resolve_config(scope), "The link was already sent.")
    assert len(sent) == 1
    assert "No reply drafted" in sent[0] and 'They said: "yes please"' in sent[0] and "The link was already sent." in sent[0]
    assert "Nothing was sent" in sent[0]


@pytest.mark.asyncio
async def test_a_bare_reaction_or_emoji_is_not_worth_an_alert(tmp_path):
    sent = []

    async def dispatch(request):
        sent.append(request)
        return {"ok": True, "message_id": "n1"}

    store, service = _service(tmp_path, dispatch)
    scope = _heard(store, "👍")
    await push_silence_notice(service, scope, service.resolve_config(scope), "A reaction needs no reply.")
    assert sent == []


@pytest.mark.asyncio
async def test_a_failed_notice_never_fails_the_turn(tmp_path):
    async def dispatch(request):
        return {"ok": False, "error": "bridge_down"}

    store, service = _service(tmp_path, dispatch)
    scope = _heard(store, "hello there")
    await push_silence_notice(service, scope, service.resolve_config(scope), "reason")
