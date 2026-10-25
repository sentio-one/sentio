"""Paused legacy transfer operations with durable imports and restartable manifests."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import time
import uuid

from gateway.business.models import AccountKey, BusinessConflict, BusinessDenied, Candidate, CheckReport, ContextEvent, Scope
from gateway.business.import_legacy import inventory_legacy
from hermes_cli.sqlite_util import transaction


def _now():
    return int(time.time()*1000)


def _inventory(params,scope):
    inventory = inventory_legacy(Path(params['path']),scope=scope)
    if inventory.source_digest != params.get('source_digest'):
        raise BusinessConflict('source_digest_mismatch')
    return inventory


def _pause(service,scope,principal):
    service.store.governor_pause_account(principal,AccountKey(scope.transport_profile,scope.account_id),reason='legacy_transfer',now_ms=_now())


async def import_records(service,params,principal,scope):
    inventory = _inventory(params,scope)
    _pause(service,scope,principal)
    imported = existing = 0
    task_ids = []
    for item in inventory.supported:
        target = str(item.get('to') or '')
        legacy_id = str(item.get('id') or '')
        if not target or not legacy_id or not isinstance(item.get('text'),str) or item.get('attachment'):
            task_ids.append(service.store.put_task(scope,dedupe_key=f'legacy:{inventory.source_digest}:{legacy_id}',reason='legacy_record_requires_manual_import',refs=(legacy_id,)))
            continue
        contact_scope = service.store.bind_contact(scope.runtime_profile,scope.transport_profile,scope.account_id,[target])
        key = (*asdict(contact_scope).values(),)
        mapping = service.store._connect().execute('SELECT 1 FROM business_legacy_imports WHERE source_digest=? AND legacy_id=? AND runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?',(inventory.source_digest,legacy_id,*key)).fetchone()
        if mapping:
            existing += 1
            continue
        source = f'legacy:{inventory.source_digest}:{legacy_id}'
        outbox_id = None
        if item['state'] == 'submitted':
            service.store.record_ingress(contact_scope,ContextEvent(source,'owner_reply','owner',item['text'],None,'legacy submitted; delivery unverified'),source_id=source)
        else:
            episode = service.store.record_ingress(contact_scope,ContextEvent(source,'message','external',item['text'],None,'legacy draft import'),source_id=source)
            revisions = service.store.list_revisions(contact_scope,episode.id)
            revision = revisions[-1] if revisions else service.store.append_revision(contact_scope,episode,Candidate('text',item['text'],{},(),(),()),expected_revision_id=None,policy_version='legacy:unreviewed',lesson_version=0)
            checks = CheckReport(False,({'code':'legacy_review_required'},),'legacy:unreviewed')
            service.store.attach_checks(contact_scope,revision.id,checks=checks,expected_context_version=episode.context_version)
            from gateway.business.render import render_delivery_plan
            plan = service.store.get_plan_by_revision(contact_scope,revision.id)
            if plan is None:
                plan = await render_delivery_plan(contact_scope,revision,now_ms=_now())
                service.store.save_plan(plan)
            # Imports deliberately remain visible despite failing the new-review gate.
            # No approval can activate this row until an operator edits and rechecks it.
            outbox_id = str(uuid.uuid5(uuid.NAMESPACE_URL, source))
            with transaction(service.store._connect(),immediate=True) as db:
                db.execute("INSERT OR IGNORE INTO business_outbox(runtime_profile,transport_profile,account_id,contact_id,outbox_id,revision_id,plan_id,request_id,state,created_at_ms,expires_at_ms,reason) VALUES(?,?,?,?,?,?,?,?,'held',?,?,?)",(*key,outbox_id,revision.id,plan.id,source,_now(),plan.expires_at_ms,'legacy_review_required'))
        with transaction(service.store._connect(),immediate=True) as db:
            db.execute('INSERT OR IGNORE INTO business_legacy_imports(source_digest,legacy_id,runtime_profile,transport_profile,account_id,contact_id,outbox_id,imported_at_ms) VALUES(?,?,?,?,?,?,?,?)',(inventory.source_digest,legacy_id,*key,outbox_id,_now()))
        imported += 1
    for item in inventory.attention:
        task_ids.append(service.store.put_task(scope,dedupe_key=f"legacy-attention:{inventory.source_digest}:{item['id']}",reason=item.get('reason','legacy_attention'),refs=(item['id'],)))
    return {'report':{'source_digest':inventory.source_digest,'imported':imported,'already_imported':existing,'task_ids':task_ids,'paused':True}}


def _persist(service,scope,principal,manifest, *, evidence=None):
    previous = service.store._connect().execute("SELECT payload FROM business_audit WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND source_id=?",(*asdict(scope).values(),manifest.id)).fetchone()
    data = json.loads(previous['payload']) if previous else {}
    data.update({'manifest':asdict(manifest),'actor':principal.subject})
    if evidence is not None:
        data['evidence'] = evidence
        data['prepared_by'] = principal.subject
    payload = json.dumps(data)
    with transaction(service.store._connect(),immediate=True) as db:
        db.execute('INSERT INTO business_audit(runtime_profile,transport_profile,account_id,contact_id,source_id,kind,reason,payload) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(runtime_profile,transport_profile,account_id,contact_id,source_id) DO UPDATE SET reason=excluded.reason,payload=excluded.payload',(*asdict(scope).values(),manifest.id,'cutover',manifest.stage,payload))


def _load(service,scope,manifest_id):
    from gateway.business.migration import CutoverManifest
    row = service.store._connect().execute("SELECT payload FROM business_audit WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND source_id=? AND kind='cutover'",(*asdict(scope).values(),manifest_id)).fetchone()
    if row is None:
        raise BusinessConflict('manifest_not_found')
    value = json.loads(row['payload'])['manifest']
    value['account'] = AccountKey(**value['account'])
    value['blockers'] = tuple(value['blockers'])
    return CutoverManifest(**value)


async def cutover(service,params,principal,scope):
    from gateway.business import migration
    if principal.kind != 'human' or 'admin' not in principal.operations:
        raise BusinessDenied()
    _pause(service,scope,principal)
    action = params['_operation']
    if action == 'prepare_cutover':
        if params.get('legacy_stopped') is not True:
            raise BusinessConflict('legacy_stopped_attestation_required')
        inventory = _inventory(params,scope)
        manifest = migration.prepare_cutover(principal,AccountKey(scope.transport_profile,scope.account_id),inventory=inventory,legacy_writers=tuple(params.get('legacy_writers',())),store=service.store)
    else:
        manifest = _load(service,scope,params['manifest_id'])
        migration._MANIFESTS[manifest.id] = manifest
        if action == 'apply_cutover':
            if params.get('source_digest') != manifest.source_digest:
                raise BusinessConflict('source_digest_mismatch')
            manifest = await migration.apply_cutover(principal,manifest.id,store=service.store)
        else:
            from gateway.business.recovery import reconcile_account
            await reconcile_account(service,manifest.account)
            manifest = await migration.rollback_cutover(principal,manifest.id,store=service.store)
            with transaction(service.store._connect(),immediate=True) as db:
                db.execute("UPDATE business_outbox SET state='invalidated',approval_id=NULL,reason='cutover_rollback' WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND state IN ('held','approved')",(scope.runtime_profile,scope.transport_profile,scope.account_id))
    evidence = {'legacy_stopped':True,'source_digest':inventory.source_digest,'inventory_path':await asyncio.to_thread(lambda: str(Path(params['path']).resolve())),'supported_ids':[item.get('id') for item in inventory.supported],'attention_ids':[item.get('id') for item in inventory.attention]} if action == 'prepare_cutover' else None
    _persist(service,scope,principal,manifest,evidence=evidence)
    return {'manifest':manifest,'paused':True}


async def execute_migration(service,params,principal):
    from gateway.business.operator_operations import _scope
    scope = _scope(service,params,principal)
    if params['_operation'] == 'import_legacy':
        return await import_records(service,params,principal,scope)
    return await cutover(service,params,principal,scope)
