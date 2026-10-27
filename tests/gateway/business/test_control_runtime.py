"""Operator controls exercise the authenticated socket and durable business ledger."""
import argparse
import asyncio
import json
import time

import pytest
import pytest_asyncio

from gateway.business.control import register_business_control_handlers
from gateway.business.models import Candidate, ContextEvent, Principal, Scope
from gateway.business.render import DeliveryPart, DeliveryPlan
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.control_socket import GatewayControlServer
from hermes_cli.business_messages import run_messages_command
from hermes_cli.subcommands.messages import build_messages_parser


def draft(store, scope):
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    episode = store.record_ingress(scope, ContextEvent('in-1', 'message', 'customer', 'Hi', None, 'inbound'), source_id=f'in-{scope.contact_id}')
    revision = store.append_revision(scope, episode, Candidate('text', 'Hello', {}, (), (), ()), expected_revision_id=None, policy_version='v1', lesson_version=0)
    plan = DeliveryPlan('plan-1', revision.id, scope, episode.context_version, 'v1', (DeliveryPart(0, 'text', {'text': 'Hello'}, None),), int(time.time()*1000)+600000, 'sha256:exact')
    store.save_plan(plan)
    receipt = store.submit_revision(scope, revision.id, request_id='req-1', now_ms=int(time.time()*1000))
    return revision, plan, receipt


@pytest_asyncio.fixture
async def runtime(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    store = BusinessStore(tmp_path)
    service = BusinessService(store)
    service.runtime_profile = 'default'
    server = GatewayControlServer(tmp_path)
    register_business_control_handlers(server, service, loop=asyncio.get_running_loop())
    assert await server.start()
    yield store, service, server
    await server.stop()


def args(*argv):
    parser = argparse.ArgumentParser()
    build_messages_parser(parser.add_subparsers(dest='command'))
    return parser.parse_args(['messages', *argv])


@pytest.mark.asyncio
async def test_parser_socket_queue_show_and_exact_approval(runtime, capsys):
    store, _, _ = runtime
    scope = Scope('default', 'whatsapp', 'account-1', '27600000001@s.whatsapp.net')
    revision, plan, receipt = draft(store, scope)
    assert await asyncio.to_thread(run_messages_command, args('queue', '--json')) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['items'][0]['revision_id'] == revision.id
    assert await asyncio.to_thread(run_messages_command, args('show', receipt.id, '--json')) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown['plan']['digest'] == plan.digest
    assert await asyncio.to_thread(run_messages_command, args('approve', revision.id, '--plan-digest', plan.digest, '--json')) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved['approval']['actor'].startswith('uid:')
    assert store._connect().execute('SELECT state FROM business_outbox').fetchone()[0] == 'approved'


@pytest.mark.asyncio
async def test_socket_refuses_missing_digest_foreign_profile_and_nonexistent(runtime):
    from gateway.business.client import request_business
    store, _, _ = runtime
    scope = Scope('default', 'whatsapp', 'account-1', 'person')
    revision, _, _ = draft(store, scope)
    async def request(operation, params):
        return await asyncio.to_thread(request_business, store.db_path.parent, operation=operation, params=params)
    assert (await request('operator.approve', {'revision_id':revision.id}))['error'] == 'plan_digest_required'
    assert (await request('operator.approve', {'revision_id':revision.id,'plan_digest':'changed'}))['error'] == 'digest_mismatch'
    foreign = {'scope':dict(runtime_profile='other', transport_profile='whatsapp', account_id='account-1', contact_id='person')}
    assert (await request('operator.queue', foreign))['error'] == 'permission_denied'
    assert (await request('operator.show', {'outbox_id':'missing'}))['ok'] is False


@pytest.mark.asyncio
async def test_cli_missing_record_and_offline_are_nonzero(runtime, capsys):
    assert await asyncio.to_thread(run_messages_command, args('show', 'missing', '--json')) == 1
    assert json.loads(capsys.readouterr().out)['ok'] is False


@pytest.mark.asyncio
async def test_digest_reaction_cannot_claim_approval_without_exact_target(runtime):
    from gateway.business.operators import handle_operator_event
    _, service, _ = runtime
    principal = Principal('operator', 'default', frozenset({'operator'}), 'human')
    result = await handle_operator_event(service, {'action':'reaction','emoji':'👍','target_message_id':'unbound'}, principal=principal)
    assert result['approval_count'] == 0
    result = await handle_operator_event(service, {'action':'shorthand','text':'yes anything here'}, principal=principal)
    assert result['approval_count'] == 0


def test_permissions_reject_foreign_scope():
    from gateway.business.permissions import authorize_operation
    from gateway.business.models import BusinessDenied
    with pytest.raises(BusinessDenied):
        authorize_operation(Principal('operator','default',frozenset({'operator'}),'human'), Scope('other','x','x','x'), 'approve')


@pytest.mark.asyncio
async def test_pause_tasks_lessons_digest_and_mode_persist_through_socket(runtime):
    from gateway.business.client import request_business
    from gateway.business.operators import handle_operator_event
    store, service, _ = runtime
    scope = Scope('default','whatsapp','account-1','person')
    revision, plan, _ = draft(store,scope)
    async def request(action, **params):
        return await asyncio.to_thread(request_business,store.db_path.parent,operation=f'operator.{action}',params=params)
    assert (await request('pause', reason='review backlog'))['paused'] is True
    assert (await request('status'))['accounts'][0]['paused'] is True
    task_id = store.put_task(scope,dedupe_key='attention',reason='check',refs=(revision.id,))
    assert (await request('tasks'))['tasks'][0]['task_id'] == task_id
    assert (await request('resolve_task',task_id=task_id,reason='reviewed'))['resolved'] is True
    assert (await request('tasks'))['tasks'][0]['resolved_by'].startswith('uid:')
    proposal = await request('propose_lessons',scope=scope.__dict__,texts=['Use verified facts'],policy_id='sales')
    lesson_id = proposal['lessons'][0]['id']
    confirmed = await request('confirm_lesson',scope=scope.__dict__,lesson_id=lesson_id)
    assert confirmed['lesson']['state'] == 'confirmed'
    assert store._connect().execute('SELECT state FROM business_lessons WHERE lesson_id=?',(lesson_id,)).fetchone()[0] == 'confirmed'
    # Lesson confirmation invalidates the old plan, so use a separate fresh target for digest approval.
    fresh = Scope('default','whatsapp','account-1','second')
    revision, plan, _ = draft(store,fresh)
    digest = (await request('digest',scope=fresh.__dict__))['digest']
    principal = Principal('operator','default',frozenset({'operator'}),'human')
    result = await handle_operator_event(service,{'action':'approve_digest','digest_id':digest['digest_id'],'revision_id':revision.id,'plan_digest':plan.digest},principal=principal)
    assert result['approval_count'] == 1
    assert (await request('set_mode',mode='shadow'))['success'] is True
    assert (await request('status'))['accounts'][0]['mode'] == 'shadow'
    assert (await request('resume'))['paused'] is False


@pytest.mark.asyncio
async def test_import_and_cutover_operations_reopen_durable_records(runtime,tmp_path):
    from gateway.business.client import request_business
    from gateway.business.import_legacy import inventory_legacy
    from gateway.business.migration import _MANIFESTS
    store,_,_ = runtime
    scope = Scope('default','sales-bot','business-phone','')
    store.bind_contact('default','sales-bot','business-phone',['person'])
    legacy = tmp_path/'legacy'
    (legacy/'pending').mkdir(parents=True)
    (legacy/'pending'/'draft.json').write_text(json.dumps({'id':'legacy-1','to':'person','text':'Imported pending text'}))
    digest = inventory_legacy(legacy,scope=scope).source_digest
    async def request(action,**params):
        return await asyncio.to_thread(request_business,store.db_path.parent,operation=f'operator.{action}',params={'scope':scope.__dict__,**params})
    imported = await request('import_legacy',path=str(legacy),source_digest=digest)
    assert imported['ok'] is True, imported
    assert imported['report']['imported'] == 1
    assert (await request('import_legacy',path=str(legacy),source_digest=digest))['report']['already_imported'] == 1
    reopened = BusinessStore(store.db_path.parent)
    row = reopened._connect().execute('SELECT * FROM business_outbox').fetchone()
    assert row['account_id'] == 'business-phone' and row['state'] == 'held'
    assert reopened._connect().execute('SELECT COUNT(*) FROM business_approvals').fetchone()[0] == 0
    refused = await request('prepare_cutover',path=str(legacy),source_digest=digest)
    assert refused['error'] == 'legacy_stopped_attestation_required'
    prepared = await request('prepare_cutover',path=str(legacy),source_digest=digest,legacy_stopped=True)
    assert prepared['manifest']['stage'] == 'ready'
    manifest_id = prepared['manifest']['id']
    _MANIFESTS.clear()
    applied = await request('apply_cutover',manifest_id=manifest_id,source_digest=digest)
    assert applied['manifest']['stage'] == 'cutover_applied'
    _MANIFESTS.clear()
    rollback = await request('rollback_cutover',manifest_id=manifest_id)
    assert rollback['manifest']['stage'] == 'rolled_back'
    paused = reopened._connect().execute('SELECT paused FROM business_governor_accounts').fetchone()[0]
    assert paused == 1
