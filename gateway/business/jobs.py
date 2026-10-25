"""Restricted native plugin API and durable drafting-job processing."""
from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import asdict

from gateway.business.models import (ActionReceipt, BusinessConflict, BusinessDenied,
                                     BusinessProtocolError, ContextEvent, Fact, Scope, parse_candidate)
from gateway.business.permissions import authorize_operation
from gateway.business import store_jobs
from hermes_cli.sqlite_util import transaction


def _text(params, name):
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise BusinessProtocolError(f'{name}_required')
    return value


def _scope(service, params, principal):
    runtime = principal.runtime_profile
    requested = params.get('scope') or {}
    if not isinstance(requested, dict):
        raise BusinessDenied('permission_denied')
    values = dict(params, **requested)
    if values.get('runtime_profile', runtime) != runtime:
        raise BusinessDenied('permission_denied')
    transport = values.get('transport_profile', runtime)
    account = values.get('account_id', values.get('account', transport))
    if transport != runtime or account != runtime:
        with transaction(service.store._connect()) as db:
            owned = db.execute('SELECT 1 FROM business_accounts WHERE runtime_profile=? AND transport_profile=? AND account_id=?', (runtime,transport,account)).fetchone()
        if not owned:
            raise BusinessDenied('permission_denied')
    contact = values.get('contact_id')
    if contact:
        with transaction(service.store._connect()) as db:
            owned = db.execute('SELECT 1 FROM business_contacts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?', (runtime,transport,account,contact)).fetchone()
        if not owned:
            raise BusinessDenied('permission_denied')
        scope = Scope(runtime,transport,account,contact)
    else:
        identifier = values.get('contact')
        scope = service.store.bind_contact(runtime,transport,account,[identifier]) if identifier else Scope(runtime,transport,account,'')
    cfg = service.resolve_config(scope)
    if cfg is None or cfg.mode == 'off':
        raise BusinessProtocolError('business_mode_off')
    return scope


def handle_plugin_request(service, operation, params, principal):
    """Record a request. Scope is bound by verified caller identity, never by payload claims."""
    try:
        if principal.kind != 'plugin':
            raise BusinessDenied('permission_denied')
        authorize_operation(principal, Scope(principal.runtime_profile,'','',''), operation)
        if operation in ('events','ack_events'):
            if operation == 'ack_events' and 'cursor' not in params:
                raise BusinessProtocolError('cursor_required')
            if params.get('runtime_profile', principal.runtime_profile) != principal.runtime_profile:
                raise BusinessDenied('permission_denied')
            return store_jobs.outcome_events(service.store,principal,_text(params,'consumer_id'),
                                             acknowledge=params.get('cursor') if operation == 'ack_events' else None)
        scope = _scope(service, params, principal)
        if operation == 'status':
            with transaction(service.store._connect()) as db:
                rows = db.execute('SELECT job_id,state,revision_id,reason FROM business_plugin_jobs WHERE plugin_id=? AND runtime_profile=? ORDER BY created_at_ms', (principal.subject,scope.runtime_profile)).fetchall()
            return {'ok':True,'status':'available','jobs':[dict(row) for row in rows]}
        if not scope.contact_id:
            raise BusinessProtocolError('contact_required')
        if operation in ('record_fact','record_action'):
            shape = Fact if operation == 'record_fact' else ActionReceipt
            payload = {key:_text(params,key) for key in shape.__dataclass_fields__}
            store_jobs.record_contribution(service.store,scope,principal.subject,'fact' if shape is Fact else 'action',payload)
            return {'ok':True,'status':'recorded','id':payload['id'],'scope':asdict(scope)}
        request_id = _text(params,'request_id')
        payload = dict(params)
        if operation == 'submit_candidate':
            candidate = params.get('candidate') or dict(kind='text',text=_text(params,'text'),data={},fact_ids=[],completed_action_ids=[],credential_slots=[])
            payload['candidate'] = asdict(parse_candidate(candidate))
            payload['intent'] = params.get('intent') or candidate['text']
        else:
            _text(params,'intent')
        row = store_jobs.submit_job(service.store,scope,principal.subject,operation,request_id,payload)
        return {'ok':True,'status':'accepted','job_id':row['job_id'],'state':row['state'],'message_id':None,'target_account':scope.account_id,'scope':asdict(scope)}
    except BusinessDenied:
        return {'ok':False,'error':'permission_denied'}
    except (BusinessProtocolError, BusinessConflict) as exc:
        return {'ok':False,'error':exc.code}
    except (TypeError, ValueError):
        return {'ok':False,'error':'invalid_request'}


async def process_jobs(service):
    """Claim each request once; an interrupted composition requires human resolution."""
    with transaction(service.store._connect()) as db:
        interrupted = db.execute("SELECT * FROM business_plugin_jobs WHERE state='composing'").fetchall()
    for row in interrupted:
        scope = Scope(*(row[key] for key in ('runtime_profile','transport_profile','account_id','contact_id')))
        service.store.put_task(scope, dedupe_key=f"plugin-job:{row['job_id']}", reason='interrupted_plugin_composition', refs=(row['job_id'],))
        with transaction(service.store._connect()) as db:
            db.execute("UPDATE business_plugin_jobs SET state='uncertain',reason='interrupted_plugin_composition' WHERE job_id=?", (row['job_id'],))
    with transaction(service.store._connect()) as db:
        pending = db.execute("SELECT * FROM business_plugin_jobs WHERE state='held' ORDER BY created_at_ms").fetchall()
    for row in pending:
        scope = Scope(*(row[key] for key in ('runtime_profile','transport_profile','account_id','contact_id')))
        with transaction(service.store._connect()) as db:
            updated = db.execute("UPDATE business_plugin_jobs SET state='composing' WHERE job_id=? AND state='held'", (row['job_id'],)).rowcount
        if not updated:
            continue
        payload = json.loads(row['payload'])
        binding = service.runtime_scope(scope) if service.runtime_scope else nullcontext()
        try:
            with binding:
                event = ContextEvent(f"plugin-job:{row['job_id']}", 'plugin_job', row['plugin_id'], payload['intent'], None, 'durable plugin request')
                episode = service.store.record_ingress(scope,event,source_id=event.id)
                from gateway.business.policy import collect_policy
                policy = await collect_policy(scope, episode, required_policies=service.resolve_config(scope).required_policies)
                result = {'decision':dict(kind='compose',reason='plugin_request',intent=payload['intent'],fact_ids=payload.get('fact_ids',[]),action_ids=payload.get('action_ids',[]),policy_version=policy.version)}
                runtime = getattr(service,'main_runtime',{})
                if row['operation'] == 'submit_candidate':
                    from gateway.business.context import build_context
                    from gateway.business.review import prepare_draft
                    from gateway.business.drafting import promote_draft
                    cfg = service.resolve_config(scope)
                    snapshot = build_context(service.store,scope,episode,policy=policy,lesson_version=0)
                    from gateway.business.models import parse_decision
                    receipt = await prepare_draft(service.store,snapshot,parse_decision(result['decision']),policy=policy,main_runtime=runtime,rewrite_attempts=cfg.rewrite_attempts,initial_candidate=parse_candidate(payload['candidate']))
                    receipt = await promote_draft(service.store,scope,receipt,cfg,request_id=row['request_id'])
                else:
                    receipt = await service.handle_turn(scope,episode,result,main_runtime=runtime)
                with transaction(service.store._connect()) as db:
                    db.execute('UPDATE business_plugin_jobs SET state=?,episode_id=?,revision_id=?,reason=? WHERE job_id=?', ('drafted' if receipt.revision_id else 'blocked',episode.id,receipt.revision_id,receipt.reason,row['job_id']))
        except Exception as exc:  # health: allow BLE001 -- failed jobs are held for explicit human recovery, never retried
            service.store.put_task(scope,dedupe_key=f"plugin-job:{row['job_id']}",reason='plugin_composition_failed',refs=(row['job_id'],))
            with transaction(service.store._connect()) as db:
                db.execute("UPDATE business_plugin_jobs SET state='failed',reason=? WHERE job_id=?", (type(exc).__name__,row['job_id']))
