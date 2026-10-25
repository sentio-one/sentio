"""Runtime contracts for paced transport, exact consumption, and early receipts."""
from dataclasses import asdict

import pytest

from gateway.business.models import AccountKey, BusinessConflict, BusinessProtocolError, Candidate, CheckReport, ContextEvent, Principal, Scope
from gateway.business.store import BusinessStore
from gateway.business.service import BusinessService
from gateway.business.config import BusinessConfig
from gateway.business.render import render_delivery_plan
from gateway.business.transport import dispatch_due
from gateway.business.receipts import DeliveryEvent, apply_delivery_event
from gateway.business.dispatch import issue_authorization, consume_authorization

NOW = 1_000_000
CFG = {'timezone': 'UTC', 'windows': {k: [{'weekdays': list(range(7)), 'start':'00:00','end':'24:00'}] for k in ('cold','reply','followup')}, 'min_gap_seconds': 10, 'caps': {}, 'hard_max_per_day':100}

async def prepared(tmp_path, *, checks=False, multipart=False, governor=CFG, now_ms=NOW):
    store = BusinessStore(tmp_path)
    scope = Scope('secondary', 'secondary', 'secondary', '123@s.whatsapp.net')
    account = AccountKey('secondary', 'secondary')
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent('in','message','customer','hello',None,'inbound'), source_id='in')
    ep = store.episode_for_source(scope,'in')
    rev = store.append_revision(scope,ep,Candidate('text','x'*5000 if multipart else 'exact  bytes\n',{},(),(),()),expected_revision_id=None,policy_version='v1',lesson_version=0)
    if checks:
        store.attach_checks(scope,rev.id,checks=CheckReport(True,(), 'v1'),expected_context_version=ep.context_version)
    plan = await render_delivery_plan(scope,rev,now_ms=now_ms)
    store.save_plan(plan)
    store.submit_revision(scope,rev.id,request_id='req',now_ms=now_ms)
    store.approve_revision(Principal('human','secondary',frozenset({'approve'}),'human'),scope,revision_id=rev.id,plan_digest=plan.digest,now_ms=now_ms)
    svc = BusinessService(store, config_resolver=lambda _: BusinessConfig('supervised',(),('human',),2,300,300,governor))
    return store,svc,scope,account,plan

@pytest.mark.asyncio
async def test_checked_revision_promotes(tmp_path):
    await prepared(tmp_path,checks=True)

@pytest.mark.asyncio
async def test_each_part_waits_for_governor_and_recovers_held_claim(tmp_path):
    _store,svc,_scope,account,_plan = await prepared(tmp_path,multipart=True)
    calls=[]
    async def send(req):
        calls.append(req)
        return {'message_id':f'msg-{len(calls)}'}
    assert (await dispatch_due(svc,account,transport_executor=send,now_ms=NOW))[0].state == 'sent'
    assert calls[0]['scope']['runtime_profile']=='secondary'
    assert await dispatch_due(svc,account,transport_executor=send,now_ms=NOW+1) == []
    assert len(calls)==1
    assert len(await dispatch_due(svc,account,transport_executor=send,now_ms=NOW+10_000))==1
    assert len(calls)==2


@pytest.mark.asyncio
async def test_future_paced_contact_does_not_block_another_contact(tmp_path):
    store,_svc,scope,account,_plan=await prepared(tmp_path,now_ms=NOW)
    delayed=store.claim_part(account,now_ms=NOW)
    from gateway.business.store_outbox import reschedule_attempt
    reschedule_attempt(store,delayed.id,not_before_ms=NOW+60_000,reason='minimum_gap')
    other=Scope(scope.runtime_profile,scope.transport_profile,scope.account_id,'456@s.whatsapp.net')
    store.bind_contact(other.runtime_profile,other.transport_profile,other.account_id,[other.contact_id])
    store.record_ingress(other,ContextEvent('other-in','message','customer','hello',None,'inbound'),source_id='other-in')
    episode=store.episode_for_source(other,'other-in')
    rev=store.append_revision(other,episode,Candidate('text','reply',{},(),(),()),expected_revision_id=None,policy_version='v1',lesson_version=0)
    other_plan=await render_delivery_plan(other,rev,now_ms=NOW)
    store.save_plan(other_plan)
    store.submit_revision(other,rev.id,request_id='other',now_ms=NOW)
    store.approve_revision(Principal('human','secondary',frozenset({'approve'}),'human'),other,revision_id=rev.id,plan_digest=other_plan.digest,now_ms=NOW)
    next_attempt=store.claim_part(account,now_ms=NOW+1)
    assert next_attempt is not None
    assert next_attempt.outbox_id != delayed.outbox_id

@pytest.mark.asyncio
async def test_missing_governor_holds_without_losing_dispatch(tmp_path):
    _store,svc,_scope,account,_plan = await prepared(tmp_path,governor=None)
    calls=[]
    async def send(req): calls.append(req); return {'message_id':'m'}
    assert await dispatch_due(svc,account,transport_executor=send,now_ms=NOW)==[]
    assert not calls
    svc._config_resolver=lambda _: BusinessConfig('supervised',(),('human',),2,300,300,CFG)

@pytest.mark.asyncio
async def test_early_receipt_joins_after_http_result_and_restart(tmp_path):
    store,svc,_scope,account,_plan = await prepared(tmp_path)
    async def send(req):
        apply_delivery_event(store,DeliveryEvent(account,'early',4,'READ',NOW))
        return {'message_id':'early'}
    await dispatch_due(svc,account,transport_executor=send,now_ms=NOW)
    reopened=BusinessStore(tmp_path)
    db=reopened._connect()
    try:
        assert db.execute('SELECT state FROM business_attempts').fetchone()['state']=='read'
        assert db.execute('SELECT state FROM business_outbox').fetchone()['state']=='delivered'
    finally: db.close()

@pytest.mark.asyncio
async def test_consumption_rechecks_claimed_scope_bytes_and_freshness(tmp_path):
    store,_svc,scope,account,plan=await prepared(tmp_path)
    attempt=store.claim_part(account,now_ms=NOW)
    store.governor_reserve_attempt(account,attempt.id,message_class='reply',now_ms=NOW,config=CFG)
    from gateway.business.readiness import _set_account_mode_row
    _set_account_mode_row(store,account,'supervised',runtime_profile='secondary')
    token=issue_authorization(account,attempt,plan,now_ms=NOW,secret_key=b'k'*32)
    req={'scope':asdict(scope),'part':asdict(plan.parts[0]),'attempt_id':attempt.id,'plan_id':plan.id,'plan_digest':plan.digest}
    bad={**req,'part':{**req['part'],'payload':{'text':'changed'}}}
    with pytest.raises(BusinessProtocolError,match='payload_mismatch'):
        consume_authorization(token,payload_digest=plan.digest,now_ms=NOW+1,store=store,secret_key=b'k'*32,request=bad)
    store.record_ingress(scope,ContextEvent('new','message','customer','new question',None,'inbound'),source_id='new')
    with pytest.raises(BusinessConflict):
        consume_authorization(token,payload_digest=plan.digest,now_ms=NOW+2,store=store,secret_key=b'k'*32,request=req)

@pytest.mark.asyncio
async def test_journal_success_reconciles_platform_id_and_early_receipt(tmp_path):
    from gateway.business.recovery import reconcile_account
    store,svc,_scope,account,_plan=await prepared(tmp_path)
    attempt=store.claim_part(account,now_ms=NOW)
    apply_delivery_event(store,DeliveryEvent(account,'journal-id',3,'DELIVERY_ACK',NOW))
    result=await reconcile_account(svc,account,journal=({'event':'attempt','attempt_id':attempt.id},{'event':'outcome','attempt_id':attempt.id,'success':True,'message_id':'journal-id','timestamp':NOW}))
    assert result[0].state=='sent'
    db=store._connect()
    try:
        assert db.execute('SELECT state FROM business_outbox').fetchone()['state']=='delivered'
    finally: db.close()


@pytest.mark.asyncio
async def test_recovery_does_not_replay_finished_attempt_or_governor_history(tmp_path):
    from gateway.business.recovery import reconcile_account
    store,svc,_scope,account,_plan=await prepared(tmp_path,now_ms=NOW)
    attempt=store.claim_part(account,now_ms=NOW)
    store.governor_reserve_attempt(account,attempt.id,message_class='reply',now_ms=NOW,config=CFG)
    store.update_attempt_state(attempt.id,state='sent',platform_ids=('message-1',))
    store.governor_complete_attempt(account,attempt.id,submitted=True,finished_at_ms=NOW+1)
    journal=({'event':'outcome','attempt_id':attempt.id,'success':True,'message_id':'message-1','timestamp':NOW},)
    assert await reconcile_account(svc,account,journal=journal)==()
    assert await reconcile_account(svc,account,journal=journal)==()
    with store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM business_governor_attempts').fetchone()[0]==1


@pytest.mark.asyncio
async def test_recovery_joins_early_receipt_after_socket_result_was_persisted(tmp_path):
    from gateway.business.recovery import reconcile_account
    store,svc,_scope,account,_plan=await prepared(tmp_path,now_ms=NOW)
    attempt=store.claim_part(account,now_ms=NOW)
    apply_delivery_event(store,DeliveryEvent(account,'early-restart',3,'DELIVERY_ACK',NOW+2))
    store.update_attempt_state(attempt.id,state='sent',platform_ids=('early-restart',))
    assert await reconcile_account(svc,account)==()
    with store._connect() as db:
        assert db.execute('SELECT state FROM business_attempts WHERE attempt_id=?',(attempt.id,)).fetchone()['state']=='delivered'
        assert db.execute('SELECT state FROM business_outbox').fetchone()['state']=='delivered'

@pytest.mark.asyncio
async def test_native_adapter_bridge_and_authenticated_consumption(tmp_path):
    import asyncio
    import json
    from pathlib import Path
    from types import SimpleNamespace
    import aiohttp
    from gateway.business.control import register_business_control_handlers
    from gateway.business.readiness import _set_account_mode_row
    from gateway.business.worker import latch_bridge_account
    from gateway.control_socket import GatewayControlServer
    from gateway.config import Platform, PlatformConfig
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter
    from hermes_constants import find_node_executable
    from tools.environments.local import served_profile_child_env
    import time
    node=find_node_executable("node")
    if node is None:
        pytest.skip("PM-managed node is not installed")
    now=int(time.time()*1000)
    store,svc,scope,account,plan=await prepared(tmp_path,now_ms=now)
    svc.runtime_profile='secondary'
    adapter=WhatsAppAdapter(PlatformConfig(enabled=True,extra={'session_path':str(tmp_path/'session')}))
    adapter._hermes_profile_name='secondary'
    adapter._business_store=store
    latch_bridge_account(adapter,account,tmp_path)
    _set_account_mode_row(store,account,'supervised',runtime_profile='secondary')
    server=GatewayControlServer(tmp_path)
    register_business_control_handlers(server,svc,loop=asyncio.get_running_loop())
    assert await server.start()
    fixture=Path(__file__).parents[1]/'fixtures'/'business_bridge.mjs'
    calls=tmp_path/'calls.jsonl'
    proc=await asyncio.create_subprocess_exec(node,str(fixture),str(adapter._session_path),str(calls),
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            env=served_profile_child_env(target_home=tmp_path,inherit_credentials=False))
    try:
        port=await asyncio.wait_for(proc.stdout.readline(),10)
        adapter._bridge_port=int(port.decode().strip())
        svc._transport_runner=SimpleNamespace(_adapters_for_profile=lambda _: {Platform.WHATSAPP:adapter})
        async with aiohttp.ClientSession() as session:
            adapter._http_session=session
            result=await dispatch_due(svc,account,now_ms=now)
        assert result[0].state=='sent'
        physical=json.loads(calls.read_text().splitlines()[0])
        assert physical['payload']['text']==plan.parts[0].payload['text']
        assert physical['chatId']==scope.contact_id
        db=store._connect()
        try:
            assert db.execute('SELECT COUNT(*) FROM business_consumed_authorizations').fetchone()[0]==1
        finally: db.close()
    finally:
        proc.terminate()
        await asyncio.wait_for(proc.wait(),10)
        await server.stop()
