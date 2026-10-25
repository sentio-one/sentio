"""Explicit operator commands cross both busy guards without becoming a turn."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.business.config import BusinessConfig
from gateway.business.models import Candidate, ContextEvent, Scope
from gateway.business.operators import handle_business_operator_message
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
