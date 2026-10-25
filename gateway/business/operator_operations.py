"""Operator operation table backed by the native durable records."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
import time
import uuid

from gateway.business.operator_migration import execute_migration
from gateway.business.models import AccountKey, BusinessConflict, BusinessDenied, BusinessProtocolError, Scope, parse_candidate
from gateway.business.permissions import authorize_operation
from hermes_cli.sqlite_util import transaction


def _now():
    return int(time.time() * 1000)


def _rows(service, table, principal, *, params=None):
    allowed = {'business_outbox','business_revisions','business_tasks','business_accounts','business_lessons'}
    if table not in allowed:
        raise BusinessProtocolError('invalid_table')
    params = params or {}
    scope = params.get('scope') or {}
    filters = {'runtime_profile': principal.runtime_profile}
    for key in ('runtime_profile','transport_profile','account_id','contact_id'):
        value = scope.get(key, params.get(key))
        if value is not None:
            filters[key] = value
    if filters['runtime_profile'] != principal.runtime_profile:
        raise BusinessDenied()
    where = ' AND '.join(f'{key}=?' for key in filters)
    return [dict(row) for row in service.store._connect().execute(f'SELECT * FROM {table} WHERE {where}', tuple(filters.values())).fetchall()]


def _scope(service, params, principal):
    explicit = params.get('scope')
    if explicit:
        scope = Scope(**explicit) if isinstance(explicit, dict) else explicit
        authorize_operation(principal, scope, 'show')
        owned = service.store._connect().execute('SELECT 1 FROM business_accounts WHERE runtime_profile=? AND transport_profile=? AND account_id=?',(scope.runtime_profile,scope.transport_profile,scope.account_id)).fetchone()
        if not owned:
            raise BusinessDenied()
        return scope
    for table, key in (('business_revisions','revision_id'), ('business_outbox','outbox_id'), ('business_tasks','task_id')):
        if params.get(key):
            rows = [r for r in _rows(service, table, principal) if r[key] == params[key]]
            if len(rows) != 1:
                raise BusinessConflict('not_found' if not rows else 'ambiguous_target')
            return Scope(**{k: rows[0][k] for k in ('runtime_profile','transport_profile','account_id','contact_id')})
    accounts = _rows(service, 'business_accounts', principal, params=params)
    if len(accounts) != 1:
        raise BusinessConflict('account_required' if not accounts else 'ambiguous_account')
    row = accounts[0]
    return Scope(row['runtime_profile'], row['transport_profile'], row['account_id'], params.get('contact_id', ''))


def _audit(service, scope, principal, action, params):
    with transaction(service.store._connect(), immediate=True) as db:
        db.execute('INSERT INTO business_audit(runtime_profile,transport_profile,account_id,contact_id,source_id,kind,reason,payload) VALUES(?,?,?,?,?,?,?,?)',
                   (*asdict(scope).values(), str(uuid.uuid4()), 'operator', action, json.dumps({'actor':principal.subject,'params':params})))


async def _queue(service, params, principal):
    from hermes_cli.business_messages import format_message_item
    rows = _rows(service, 'business_outbox', principal, params=params)
    items = [format_message_item(service.store, Scope(r['runtime_profile'],r['transport_profile'],r['account_id'],r['contact_id']),r['outbox_id']) for r in rows]
    return {'items': items}


async def _show(service, params, principal):
    scope = _scope(service, params, principal)
    revision_id = params.get('revision_id')
    item = None
    if params.get('outbox_id'):
        from hermes_cli.business_messages import format_message_item
        item = format_message_item(service.store, scope, params['outbox_id'])
        revision_id = item.get('revision_id')
    if not revision_id:
        raise BusinessProtocolError('revision_id_required')
    revision = service.store.get_revision(scope, revision_id)
    plan = service.store.get_plan_by_revision(scope, revision_id)
    return {'item':item, 'revision':revision, 'plan':plan}


async def _approve(service, params, principal):
    if not params.get('plan_digest'):
        raise BusinessProtocolError('plan_digest_required')
    scope = _scope(service, params, principal)
    revision = service.store.get_revision(scope,params['revision_id'])
    latest = service.store.list_revisions(scope,revision.episode.id)[-1]
    if latest.id != revision.id:
        raise BusinessConflict('stale_revision')
    approval = await service.approve_revision(principal, scope, revision_id=params['revision_id'], plan_digest=params['plan_digest'], override_reason=params.get('override_reason'))
    _audit(service, scope, principal, 'approve', {'revision_id':params['revision_id'],'plan_digest':params['plan_digest']})
    return {'approval':approval}


async def _reject(service, params, principal):
    scope = _scope(service, params, principal)
    receipt = await service.reject_revision(principal, scope, revision_id=params['revision_id'], reason=params.get('reason') or 'operator_rejected')
    _audit(service, scope, principal, 'reject', params)
    return {'receipt':receipt}


async def _edit(service, params, principal):
    from gateway.business.context import build_context
    from gateway.business.policy import check_candidate, collect_policy
    from gateway.business.review import review_candidate
    from gateway.business.render import render_delivery_plan
    scope = _scope(service, params, principal)
    old = service.store.get_revision(scope, params['revision_id'])
    candidate = parse_candidate(params['candidate']) if 'candidate' in params else replace(old.candidate, text=params['text'])
    policy = await collect_policy(scope, old.episode)
    snapshot = build_context(service.store, scope, old.episode, policy=policy, lesson_version=old.lesson_version)
    revision = service.store.append_revision(scope, old.episode, candidate, expected_revision_id=old.id, policy_version=policy.version, lesson_version=old.lesson_version)
    # Replacement invalidates every prior held/approved plan before advisory I/O.
    with transaction(service.store._connect(), immediate=True) as db:
        db.execute("UPDATE business_outbox SET state='invalidated',reason='operator_edit' WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=? AND state IN ('held','approved')", (*asdict(scope).values(),old.id))
    checks = await check_candidate(scope, candidate, bundle=policy)
    revision = service.store.attach_checks(scope, revision.id, checks=checks, expected_context_version=old.episode.context_version)
    if not checks.ok:
        return {'revision':revision,'state':'attention','reason':'checks_failed'}
    runtime = getattr(service, 'main_runtime', None) or {}
    review = await review_candidate(snapshot, revision, main_runtime=runtime)
    revision = service.store.attach_review(scope, revision.id, verdict=review, expected_context_version=old.episode.context_version)
    plan = await render_delivery_plan(scope, revision, now_ms=_now())
    service.store.save_plan(plan)
    receipt = await service.submit_revision(scope, revision.id, request_id=f'operator-edit:{revision.id}')
    _audit(service, scope, principal, 'edit', {'revision_id':revision.id,'parent_revision_id':old.id})
    return {'revision':revision, 'plan':plan, 'receipt':receipt}


async def _tasks(service, params, principal):
    rows = _rows(service,'business_tasks',principal,params=params)
    return {'tasks': [{**r,'payload':json.loads(r['payload'])} for r in rows]}


async def _resolve_task(service, params, principal):
    scope = _scope(service, params, principal)
    reason = params.get('reason') or params.get('resolution')
    if not reason:
        raise BusinessProtocolError('resolution_required')
    with transaction(service.store._connect(), immediate=True) as db:
        count = db.execute('UPDATE business_tasks SET resolved_at_ms=?,resolution=?,resolved_by=? WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND task_id=? AND resolved_at_ms IS NULL', (_now(),reason,principal.subject,*asdict(scope).values(),params['task_id'])).rowcount
    return {'resolved':bool(count), 'task_id':params['task_id']}


async def _pause(service, params, principal):
    scope = _scope(service, params, principal)
    account = AccountKey(scope.transport_profile,scope.account_id)
    reason = params.get('reason') or 'operator_paused'
    service.store.governor_pause_account(principal, account, reason=reason, now_ms=_now())
    _audit(service,scope,principal,'pause',{'reason':reason})
    return {'paused':True}


async def _resume(service, params, principal):
    scope = _scope(service, params, principal)
    account = AccountKey(scope.transport_profile,scope.account_id)
    service.store.governor_resume_account(principal, account, now_ms=_now())
    _audit(service,scope,principal,'resume',{})
    return {'paused':False}


async def _digest(service, params, principal):
    from gateway.business.operators import create_digest
    scope = _scope(service, params, principal)
    rows = _rows(service,'business_outbox',principal,params={'scope':asdict(scope)})
    revisions = tuple(r['revision_id'] for r in rows if r['state'] == 'held')
    return {'digest':create_digest(principal,scope,revisions,store=service.store)}


async def _reconcile(service, params, principal):
    from gateway.business.recovery import reconcile_account
    scope = _scope(service, params, principal)
    receipts = await reconcile_account(service,AccountKey(scope.transport_profile,scope.account_id))
    _audit(service,scope,principal,'reconcile',{})
    return {'receipts':receipts}


async def _status(service, params, principal):
    from gateway.business.readiness import get_account_mode, get_bridge_state
    rows = _rows(service,'business_accounts',principal,params=params)
    accounts = []
    for row in rows:
        account = AccountKey(row['transport_profile'],row['account_id'])
        paused = service.store._connect().execute('SELECT paused,pause_reason FROM business_governor_accounts WHERE transport_profile=? AND account_id=?',(account.transport_profile,account.account_id)).fetchone()
        accounts.append({**row,'mode':get_account_mode(service.store,account,principal.runtime_profile),'bridge':get_bridge_state(account,store=service.store),'paused':bool(paused and paused['paused']),'pause_reason':paused['pause_reason'] if paused else None})
    return {'accounts':accounts}


async def _readiness(service, params, principal):
    from gateway.business.readiness import check_readiness
    scope = _scope(service,params,principal)
    return {'readiness':await check_readiness(service,AccountKey(scope.transport_profile,scope.account_id),runtime_profile=scope.runtime_profile)}


async def _mode(service, params, principal):
    from gateway.business.readiness import set_business_mode
    if principal.kind != 'human' or 'admin' not in principal.operations:
        raise BusinessDenied()
    scope = _scope(service,params,principal)
    result = await set_business_mode(principal,AccountKey(scope.transport_profile,scope.account_id),mode=params['mode'],service=service,runtime_profile=scope.runtime_profile)
    if not result.get('success'):
        raise BusinessConflict(','.join(result.get('blocking',())) or 'mode_change_refused')
    _audit(service,scope,principal,'set_mode',{'mode':params['mode']})
    return result


async def _lessons(service, params, principal):
    from gateway.business import learning
    scope = _scope(service,params,principal)
    action = params['_operation']
    if action == 'propose_lessons':
        return {'lessons':learning.propose_lessons(principal,scope,policy_id=params.get('policy_id','default'),texts=tuple(params.get('texts',())),evidence_revision_ids=tuple(params.get('evidence_revision_ids',())),store=service.store)}
    kwargs = {'reason':params['reason']} if action == 'revoke_lesson' else {}
    return {'lesson':getattr(learning,action)(principal,scope,params['lesson_id'],store=service.store,**kwargs)}


async def _credential(service, params, principal):
    from gateway.business.credentials import grant_credential
    scope = _scope(service,params,principal)
    grant = grant_credential(principal,scope,vault_item_id=params['vault_item_id'],expires_at_ms=params['expires_at_ms'])
    with transaction(service.store._connect(),immediate=True) as db:
        db.execute('INSERT INTO business_credential_grants(grant_id,runtime_profile,transport_profile,account_id,contact_id,payload,created_at_ms) VALUES(?,?,?,?,?,?,?)',(grant.id,*asdict(scope).values(),json.dumps(asdict(grant)),_now()))
    return {'grant':grant}


async def _inventory(service, params, principal):
    from pathlib import Path
    from gateway.business.import_legacy import inventory_legacy
    scope = _scope(service,params,principal)
    return {'inventory':inventory_legacy(Path(params['path']), scope=scope)}


async def _submit_candidate(service, params, principal):
    from gateway.business.transport import submit_external
    scope = _scope(service,params,principal)
    if not scope.contact_id or not params.get('request_id'):
        raise BusinessProtocolError('explicit_target_and_request_required')
    candidate = parse_candidate(params['candidate'])
    receipt = await submit_external(scope,candidate,request_id=params['request_id'],source_kind='local_control',service=service,main_runtime=getattr(service,'main_runtime',{}) or {})
    return {'receipt':receipt}


OPERATIONS = {
    'submit_candidate':_submit_candidate, 'queue':_queue, 'show':_show, 'edit':_edit, 'approve':_approve, 'reject':_reject,
    'tasks':_tasks,'resolve_task':_resolve_task,'pause':_pause,'resume':_resume,
    'digest':_digest,'recard':_show,'reconcile':_reconcile,'status':_status,
    'readiness':_readiness,'set_mode':_mode,'propose_lessons':_lessons,
    'confirm_lesson':_lessons,'revoke_lesson':_lessons,'credential_grant':_credential,
    'inventory':_inventory,'import_legacy':execute_migration,'prepare_cutover':execute_migration,
    'apply_cutover':execute_migration,'rollback_cutover':execute_migration,
}


async def execute_operator_operation(service, action, params, principal):
    handler = OPERATIONS.get(action)
    if handler is None:
        raise BusinessProtocolError('unknown_operation')
    scope = params.get('scope')
    check_scope = Scope(**scope) if isinstance(scope,dict) else Scope(principal.runtime_profile,'','','')
    authorize_operation(principal,check_scope,action)
    return await handler(service,{**params,'_operation':action},principal)
