"""Immutable digest review and authenticated WhatsApp operator commands."""
from __future__ import annotations

from dataclasses import asdict
import json
import shlex
import time
import uuid
from typing import Any

from gateway.business.models import BusinessConflict, BusinessDenied, Principal, Scope
from gateway.business.permissions import OPERATOR_OPERATIONS, authorize_operation
from hermes_cli.sqlite_util import transaction


class OperatorResult(dict):
    """Operator result usable by transports and existing callers."""
    def __getattr__(self, item):
        return self.get(item)


def create_digest(principal, scope, revision_ids, store=None):
    authorize_operation(principal,scope,'digest')
    if store is None:
        raise BusinessConflict('store_required')
    digests = {}
    for revision_id in revision_ids:
        plan = store.get_plan_by_revision(scope,revision_id)
        if plan is None:
            raise BusinessConflict('plan_not_found')
        digests[revision_id] = plan.digest
    digest_id = f'dig-{uuid.uuid4().hex[:12]}'
    now_ms = int(time.time()*1000)
    with transaction(store._connect(),immediate=True) as db:
        db.execute('INSERT INTO business_digests(digest_id,runtime_profile,transport_profile,account_id,contact_id,revision_ids,created_at_ms,plan_digests) VALUES(?,?,?,?,?,?,?,?)',(digest_id,*asdict(scope).values(),json.dumps(list(revision_ids)),now_ms,json.dumps(digests)))
    return {'digest_id':digest_id,'scope':scope,'revision_ids':revision_ids,'plan_digests':digests,'created_at_ms':now_ms}


async def handle_operator_event(service,event,*,principal):
    action = str(event.get('action',''))
    scope = event.get('scope')
    if isinstance(scope,dict):
        scope = Scope(**scope)
    scope = scope or Scope(principal.runtime_profile,'','','')
    try:
        authorize_operation(principal,scope,action)
        if action in {'reaction','shorthand','approve_digest'}:
            return await _approve_digest(service,event,principal)
        if action == 'alert':
            key = str(event.get('dedupe_key') or '')
            task = service.store.put_task(scope,dedupe_key=f'alert:{key}',reason=str(event.get('text') or 'operator_alert'),refs=())
            return OperatorResult(alert_id=task,emitted=True,approval_count=0)
        return OperatorResult(error='unknown_operation',approval_count=0)
    except BusinessDenied:
        return OperatorResult(error='permission_denied',approval_count=0)
    except BusinessConflict as exc:
        return OperatorResult(error=str(exc),approval_count=0)


async def _approve_digest(service,event,principal):
    from gateway.business.control import execute_business_request
    revision_id = event.get('revision_id')
    if not revision_id:
        return OperatorResult(error='ambiguous_command',approval_count=0)
    row = service.store._connect().execute('SELECT r.*,c.context_version AS current_version,e.context_version AS episode_version FROM business_revisions r JOIN business_contacts c USING(runtime_profile,transport_profile,account_id,contact_id) JOIN business_episodes e USING(runtime_profile,transport_profile,account_id,contact_id,episode_id) WHERE r.revision_id=? AND r.runtime_profile=?',(revision_id,principal.runtime_profile)).fetchone()
    if not row:
        return OperatorResult(error='revision_not_found',approval_count=0)
    if row['current_version'] != row['episode_version']:
        return OperatorResult(error='stale_revision',approval_count=0)
    digest = service.store._connect().execute('SELECT * FROM business_digests WHERE digest_id=? AND runtime_profile=?',(event.get('digest_id'),principal.runtime_profile)).fetchone()
    if not digest or revision_id not in json.loads(digest['revision_ids']):
        return OperatorResult(error='unbound_digest',approval_count=0)
    expected = json.loads(digest['plan_digests']).get(revision_id)
    if not expected or event.get('plan_digest') != expected:
        return OperatorResult(error='digest_mismatch',approval_count=0)
    result = await execute_business_request(service,operation='operator.approve',params={'revision_id':revision_id,'plan_digest':expected},principal=principal)
    return OperatorResult(**result,approval_count=1 if result.get('ok') else 0)


def is_business_operator_command(event):
    platform = getattr(getattr(event.source,'platform',None),'value',None)
    text = (getattr(event,'text','') or '').strip()
    return platform == 'whatsapp' and text.split(maxsplit=1)[0:1] == ['/messages'] and bool(getattr(event,'allow_gateway_control',False))


async def handle_business_operator_message(runner,event) -> str | None:
    """Return a control reply only for an explicit verified operator command."""
    if not is_business_operator_command(event):
        return None
    service = getattr(runner,'_business_service',None)
    if service is None:
        return None
    from gateway.session_identity import identity_of
    identity = identity_of(event.source)
    runtime = identity.runtime_profile if identity else getattr(event.source,'profile',None) or 'default'
    transport = identity.transport_profile if identity else runtime
    scope = Scope(runtime,transport,transport,event.source.chat_id)
    config = service.resolve_config(scope)
    # Operator routing is a configured, alias-resolved DM; customer-supplied metadata grants nothing.
    # Anything that is not a verified operator DM is an ordinary inbound message and must be recorded as one.
    if not config or getattr(event.source,'chat_type',None) != 'dm':
        return None
    destination = _operator_destination(service,scope,event.source,config.operators)
    if not destination or not runner._is_user_authorized_for_source(event.source):
        return None
    scope = Scope(runtime,transport,transport,destination)
    from gateway.business.control import execute_business_request
    from hermes_cli.subcommands.messages import build_messages_parser
    import argparse
    class CommandParser(argparse.ArgumentParser):
        def error(self,message):
            raise ValueError(message)
        def exit(self,status=0,message=None):
            raise ValueError(message or 'help_requested')
    parser = CommandParser(add_help=False)
    build_messages_parser(parser.add_subparsers(dest='command'))
    try:
        parsed = parser.parse_args(['messages',*shlex.split(event.text.strip())[1:]])
        action = 'set_mode' if parsed.messages_command == 'mode' else (parsed.messages_command or '').replace('-','_')
        params = {k:v for k,v in vars(parsed).items() if k not in {'command','messages_command','func','json'} and v is not None}
        if params.get('runtime_profile',runtime) != runtime:
            raise BusinessDenied()
        params['runtime_profile'] = runtime
        principal = Principal(f'whatsapp:{event.source.user_id}',runtime,OPERATOR_OPERATIONS,'human')
        result = await execute_business_request(service,operation=f'operator.{action}',params=params,principal=principal)
    except (ValueError,BusinessDenied) as exc:
        result = {'ok':False,'error':str(exc) or 'permission_denied'}
    from gateway.business.transport import send_operator_notice
    notice = await send_operator_notice(service,scope,json.dumps(result,ensure_ascii=False))
    if not notice.get('ok'):
        service.store.put_task(scope,dedupe_key=f"operator-notice:{event.message_id or uuid.uuid4()}",reason=str(notice.get('error') or 'operator_notice_unavailable'),refs=(json.dumps(result,ensure_ascii=False),))
    return ''


def _operator_destination(service,scope,source,operators):
    from gateway.whatsapp_identity import to_whatsapp_jid
    from gateway.business.store_episodes import _alias_key
    incoming = {_alias_key(value) for value in (source.user_id,source.chat_id) if value}
    configured = {_alias_key(value) for value in operators}
    for value in operators:
        if _alias_key(value) in incoming:
            return to_whatsapp_jid(value)
    # Alias membership is trusted only after the store resolved the configured identifier.
    rows = service.store._connect().execute('SELECT alias,contact_id FROM business_aliases WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND held=0',(scope.runtime_profile,scope.transport_profile,scope.account_id)).fetchall()
    owners = {r['contact_id'] for r in rows if r['alias'] in configured}
    matched = owners & {r['contact_id'] for r in rows if r['alias'] in incoming}
    for value in operators:
        if any(r['alias'] == _alias_key(value) and r['contact_id'] in matched for r in rows):
            return to_whatsapp_jid(value)
    return None
