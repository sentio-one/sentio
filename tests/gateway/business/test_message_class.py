"""Pacing class is derived from the conversation, never assumed to be a reply."""

import pytest

from gateway.business.models import AccountKey, Candidate, ContextEvent, Principal, Scope
from gateway.business.render import render_delivery_plan
from gateway.business.service import BusinessService
from gateway.business.config import BusinessConfig
from gateway.business.store import BusinessStore
from gateway.business.transport import dispatch_due

NOW = 1_000_000
ALL_DAY = [{"weekdays": list(range(7)), "start": "00:00", "end": "24:00"}]
GOVERNOR = {
    "timezone": "UTC",
    "windows": {"cold": [], "reply": ALL_DAY, "followup": ALL_DAY},
    "min_gap_seconds": 10,
    "caps": {},
    "hard_max_per_day": 100,
}
SCOPE = Scope("default", "default", "default", "123@s.whatsapp.net")
ACCOUNT = AccountKey("default", "default")


async def _submit(store, source_id, text, *, evidence, approve=True):
    episode = store.record_ingress(SCOPE, ContextEvent(source_id, "message", "customer", text, None, evidence), source_id=source_id)
    revision = store.append_revision(SCOPE, episode, Candidate("text", f"re: {text}", {}, (), (), ()),
                                     expected_revision_id=None, policy_version="v1", lesson_version=0)
    plan = await render_delivery_plan(SCOPE, revision, now_ms=NOW)
    store.save_plan(plan)
    receipt = store.submit_revision(SCOPE, revision.id, request_id=f"req-{source_id}", now_ms=NOW)
    if approve:
        store.approve_revision(Principal("human", "default", frozenset({"approve"}), "human"), SCOPE,
                               revision_id=revision.id, plan_digest=plan.digest, now_ms=NOW)
    return receipt.id


def _class(store, outbox_id):
    with store._connect() as db:
        return db.execute("SELECT message_class FROM business_outbox WHERE outbox_id=?", (outbox_id,)).fetchone()[0]


@pytest.mark.asyncio
async def test_classes_follow_the_conversation(tmp_path):
    store = BusinessStore(tmp_path)
    store.bind_contact("default", "default", "default", [SCOPE.contact_id])
    cold = await _submit(store, "out-1", "intro", evidence="outbound", approve=False)
    assert _class(store, cold) == "cold"
    reply = await _submit(store, "in-1", "hello", evidence="inbound", approve=False)
    assert _class(store, reply) == "reply"
    # Having heard from the customer, a later producer-initiated message is a follow-up, not cold.
    followup = await _submit(store, "out-2", "checking in", evidence="outbound", approve=False)
    assert _class(store, followup) == "followup"


@pytest.mark.asyncio
async def test_cold_outreach_is_not_paced_with_reply_windows(tmp_path):
    store = BusinessStore(tmp_path)
    store.bind_contact("default", "default", "default", [SCOPE.contact_id])
    service = BusinessService(store, config_resolver=lambda _: BusinessConfig("supervised", (), ("human",), 2, 300, 300, GOVERNOR))
    await _submit(store, "out-1", "intro", evidence="outbound")
    sent = []

    async def network(request):
        sent.append(request)
        return {"message_id": "m"}

    # The cold window is closed in this configuration; the reply window is open.
    assert await dispatch_due(service, ACCOUNT, transport_executor=network, now_ms=NOW) == []
    assert sent == []
