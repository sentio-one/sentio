"""Explicit operator commands cross both busy guards without becoming a turn."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.business.config import BusinessConfig
from gateway.business.models import Candidate, ContextEvent, Scope
from gateway.business.operators import handle_business_operator_message, handle_business_operator_reaction
from gateway.business.render import render_delivery_plan
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource


class Adapter(BasePlatformAdapter):
    async def connect(self, *, is_reconnect=False):
        return True
    async def disconnect(self):
        pass
    async def get_chat_info(self, chat_id):
        return {'type':'private'}
    async def send(self,chat_id,content,reply_to=None,metadata=None):
        self.sent.append(content)
        return SendResult(success=True,message_id='card')


def setup(tmp_path):
    store = BusinessStore(tmp_path)
    cfg = BusinessConfig('shadow',(),('123',),2,300,600,None)
    service = BusinessService(store,config_resolver=cfg)
    service.runtime_profile = 'default'
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner._business_service = service
    runner._is_user_authorized_for_source = lambda source: True
    runner._admit_bot_message_for_source = lambda source: True
    runner._effective_busy_input_mode = lambda source:'queue'
    runner._draining = False
    adapter = Adapter(PlatformConfig(enabled=True),Platform.WHATSAPP)
    adapter.sent = []
    source = SessionSource(platform=Platform.WHATSAPP,chat_id='123@s.whatsapp.net',chat_type='dm',user_id='123@s.whatsapp.net')
    event = MessageEvent(text='/messages queue --json',message_type=MessageType.TEXT,source=source)
    runner._delivery_adapter_for = lambda source:adapter
    runner.adapters = {Platform.WHATSAPP:adapter}
    service.runner = runner
    async def dispatch(request):
        from gateway.business.control import execute_business_request
        from gateway.business.models import Principal
        result = await execute_business_request(service,operation='business.consume',params=request,principal=Principal('bridge','default',frozenset({'transport'}),'transport'))
        assert result['ok'], result
        adapter.sent.append(request['part']['payload']['text'])
        return {'ok':True,'message_id':'operator-card'}
    adapter.dispatch_business_part = dispatch
    return runner, adapter, event


@pytest.mark.asyncio
async def test_base_active_guard_dispatches_control_inline(tmp_path):
    runner,adapter,event = setup(tmp_path)
    calls = []
    async def handler(incoming):
        calls.append(incoming)
        return await handle_business_operator_message(runner,incoming)
    adapter.set_message_handler(handler)
    key = adapter._event_session_key(event)
    active = asyncio.Event()
    adapter._active_sessions[key] = active
    await adapter._handle_message_while_active(event,key)
    assert calls == [event]
    assert not adapter._pending_messages
    assert adapter._active_sessions[key] is active
    assert not active.is_set()
    assert '"ok": true' in adapter.sent[0]


@pytest.mark.asyncio
async def test_runner_busy_guard_routes_command_before_queueing(tmp_path):
    runner,adapter,event = setup(tmp_path)
    runner._send_busy_reply = AsyncMock()
    assert await runner._handle_active_session_busy_message(event,'active') is True
    assert runner._send_busy_reply.await_count == 0
    assert '"ok": true' in adapter.sent[0]
    assert not adapter._pending_messages


@pytest.mark.asyncio
async def test_customer_command_cannot_gain_operator_access(tmp_path):
    runner,adapter,event = setup(tmp_path)
    event.source.user_id = 'other@s.whatsapp.net'
    event.source.chat_id = 'other@s.whatsapp.net'
    # Not a verified operator: the text is an ordinary inbound message, never silently dropped.
    assert await handle_business_operator_message(runner,event) is None
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_verified_command_uses_signed_one_use_notice(tmp_path):
    from gateway.business.control import execute_business_request
    from gateway.business.models import Principal
    runner,adapter,event = setup(tmp_path)
    runner.adapters = {Platform.WHATSAPP:adapter}
    runner._business_service.runner = runner
    requests = []
    async def dispatch(request):
        result = await execute_business_request(runner._business_service,operation='business.consume',params=request,principal=Principal('bridge','default',frozenset({'transport'}),'transport'))
        assert result['ok'], result
        requests.append(request)
        return {'ok':True,'message_id':'operator-card'}
    adapter.dispatch_business_part = dispatch
    result = await handle_business_operator_message(runner,event)
    assert result == ''
    assert len(requests) == 1
    assert '"ok": true' in requests[0]['part']['payload']['text']
    duplicate = await execute_business_request(runner._business_service,operation='business.consume',params=requests[0],principal=Principal('bridge','default',frozenset({'transport'}),'transport'))
    assert duplicate['ok'] is False


@pytest.mark.asyncio
async def test_whatsapp_digest_approval_routes_through_operator_event(tmp_path,monkeypatch):
    runner,adapter,event=setup(tmp_path)
    event.text='/messages approve revision-1 --plan-digest sha256:exact --digest-id dig-1'
    handler=AsyncMock(return_value={'ok':True,'approval_count':1})
    monkeypatch.setattr('gateway.business.operators.handle_operator_event',handler)
    async def dispatch(request):
        adapter.sent.append(request['part']['payload']['text'])
        return {'ok':True,'message_id':'operator-card'}
    adapter.dispatch_business_part=dispatch
    assert await handle_business_operator_message(runner,event)==''
    assert handler.await_count==1
    payload=handler.await_args.args[1]
    assert payload['action']=='approve_digest'
    assert payload['digest_id']=='dig-1'
    assert payload['plan_digest']=='sha256:exact'


@pytest.mark.asyncio
async def test_whatsapp_reaction_uses_the_exact_sent_review_card(tmp_path,monkeypatch):
    from gateway.session_identity import identity_of
    runner,_adapter,event=setup(tmp_path)
    identity=identity_of(event.source)
    runtime=identity.runtime_profile if identity else 'default'
    transport=identity.transport_profile if identity else runtime
    with runner._business_service.store._connect() as db:
        db.execute('INSERT INTO business_operator_cards(message_id,runtime_profile,transport_profile,account_id,contact_id,digest_id,revision_id,plan_digest,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?)',
                   ('card',runtime,transport,transport,'customer@s.whatsapp.net','digest','revision','sha256:exact',1))
    event.metadata={'reaction_target_id':'card','reaction_emoji':'👍'}
    event.message_id='reaction-1'
    event.text=''
    approval=AsyncMock(return_value={'approval_count':1})
    notice=AsyncMock(return_value={'ok':True})
    monkeypatch.setattr('gateway.business.operators.handle_operator_event',approval)
    monkeypatch.setattr('gateway.business.transport.send_operator_notice',notice)
    assert await handle_business_operator_reaction(runner,event) is True
    payload=approval.await_args.args[1]
    assert payload['action']=='reaction'
    assert payload['revision_id']=='revision'
    assert payload['plan_digest']=='sha256:exact'
    assert payload['target_message_id']=='card'
    assert notice.await_count==1


@pytest.mark.asyncio
async def test_whatsapp_recard_notice_persists_reaction_binding(tmp_path):
    from gateway.session_identity import identity_of
    runner,_adapter,event=setup(tmp_path)
    identity=identity_of(event.source)
    runtime=identity.runtime_profile if identity else 'default'
    transport=identity.transport_profile if identity else runtime
    customer=Scope(runtime,transport,transport,'customer@s.whatsapp.net')
    store=runner._business_service.store
    store.bind_contact(runtime,transport,transport,[customer.contact_id])
    store.record_ingress(customer,ContextEvent('in','message','customer','hello',None,'inbound'),source_id='in')
    episode=store.episode_for_source(customer,'in')
    revision=store.append_revision(customer,episode,Candidate('text','approved copy',{},(),(),()),expected_revision_id=None,policy_version='v1',lesson_version=0)
    plan=await render_delivery_plan(customer,revision,now_ms=int(time.time()*1000))
    store.save_plan(plan)
    event.text=f'/messages recard {revision.id}'
    await handle_business_operator_message(runner,event)
    with store._connect() as db:
        row=db.execute('SELECT digest_id,revision_id,plan_digest FROM business_operator_cards WHERE message_id=?',('operator-card',)).fetchone()
    assert row['revision_id']==revision.id
    assert row['plan_digest']==plan.digest
    assert row['digest_id']
