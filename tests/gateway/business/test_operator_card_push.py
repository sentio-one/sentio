"""A held draft is pushed to every configured operator as a review card."""
import pytest

from gateway.business.config import BusinessConfig
from gateway.business.models import Candidate, ContextEvent, Principal, Scope
from gateway.business.operator_cards import push_review_cards
from gateway.business.render import DeliveryPart, DeliveryPlan
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from gateway.run import GatewayRunner

OPERATOR = "27648873157"
CUSTOMER = "27829533285@s.whatsapp.net"


class Adapter(BasePlatformAdapter):
    async def connect(self, *, is_reconnect=False):
        return True

    async def disconnect(self):
        pass

    async def get_chat_info(self, chat_id):
        return {"type": "private"}

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise AssertionError("cards travel through the signed business lane")


def _held_draft(store):
    scope = Scope("default", "default", "default", CUSTOMER)
    store.bind_contact("default", "default", "default", [CUSTOMER])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="s1")
    episode = store.episode_for_source(scope, "s1")
    rev = store.append_revision(scope, episode, Candidate("text", "Hi. How can I help?", {}, (), (), ()),
                                expected_revision_id=None, policy_version="v1", lesson_version=0)
    store.save_plan(DeliveryPlan("p1", rev.id, scope, episode.context_version, "v1",
                                 (DeliveryPart(0, "text", {"text": "Hi. How can I help?"}, None),), 2_000_000_000_000, "sha256:d1"))
    store.submit_revision(scope, rev.id, request_id="req-1", now_ms=1000)
    return scope, rev


def _service(tmp_path, dispatch):
    store = BusinessStore(tmp_path)
    service = BusinessService(store, config_resolver=BusinessConfig("supervised", (), (OPERATOR,), 2, 300, 600, None))
    service.runtime_profile = "default"
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    adapter = Adapter(PlatformConfig(enabled=True), Platform.WHATSAPP)
    adapter.dispatch_business_part = dispatch
    runner._delivery_adapter_for = lambda source: adapter
    runner.adapters = {Platform.WHATSAPP: adapter}
    service.runner = runner
    return store, service


async def _consume(service, request):
    from gateway.business.control import execute_business_request
    result = await execute_business_request(service, operation="business.consume", params=request,
                                            principal=Principal("bridge", "default", frozenset({"transport"}), "transport"))
    assert result["ok"], result


@pytest.mark.asyncio
async def test_held_draft_is_pushed_and_recorded_for_a_reaction(tmp_path):
    sent = []

    async def dispatch(request):
        await _consume(service, request)
        sent.append(request["part"]["payload"]["text"])
        return {"ok": True, "message_id": "card-1"}

    store, service = _service(tmp_path, dispatch)
    scope, rev = _held_draft(store)

    await push_review_cards(service, scope, service.resolve_config(scope), rev.id)

    assert len(sent) == 1 and "Hi. How can I help?" in sent[0] and "👍" in sent[0]
    row = store._connect().execute("SELECT revision_id, plan_digest FROM business_operator_cards WHERE message_id='card-1'").fetchone()
    assert row["revision_id"] == rev.id and row["plan_digest"] == "sha256:d1"


@pytest.mark.asyncio
async def test_failed_notice_raises_a_task_and_never_the_turn(tmp_path):
    async def dispatch(request):
        return {"ok": False, "error": "bridge_down"}

    store, service = _service(tmp_path, dispatch)
    scope, rev = _held_draft(store)

    await push_review_cards(service, scope, service.resolve_config(scope), rev.id)

    task = store._connect().execute("SELECT payload FROM business_tasks WHERE dedupe_key=?", (f"operator-notice:{rev.id}",)).fetchone()
    assert task and "bridge_down" in task["payload"]
