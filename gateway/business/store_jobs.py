"""Durable plugin requests, contributions, and acknowledged outcome streams."""
from __future__ import annotations

import json
import time
import uuid

from gateway.business.models import ActionReceipt, BusinessConflict, Fact
from gateway.business.store_episodes import _scope_values
from hermes_cli.sqlite_util import transaction

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS business_plugin_jobs (
 job_id TEXT PRIMARY KEY, plugin_id TEXT NOT NULL, runtime_profile TEXT NOT NULL,
 transport_profile TEXT NOT NULL, account_id TEXT NOT NULL, contact_id TEXT NOT NULL,
 request_id TEXT NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'held', episode_id TEXT, revision_id TEXT, reason TEXT,
 created_at_ms INTEGER NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(plugin_id,runtime_profile,request_id));
CREATE TABLE IF NOT EXISTS business_plugin_contributions (
 plugin_id TEXT NOT NULL, runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL,
 account_id TEXT NOT NULL, contact_id TEXT NOT NULL, kind TEXT NOT NULL,
 item_id TEXT NOT NULL, payload TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(plugin_id,runtime_profile,transport_profile,account_id,contact_id,kind,item_id));
CREATE TABLE IF NOT EXISTS business_plugin_outcomes (
 cursor INTEGER PRIMARY KEY AUTOINCREMENT, runtime_profile TEXT NOT NULL,
 transport_profile TEXT NOT NULL, account_id TEXT NOT NULL, contact_id TEXT NOT NULL,
 event_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS business_plugin_cursors (
 plugin_id TEXT NOT NULL, runtime_profile TEXT NOT NULL, consumer_id TEXT NOT NULL,
 acknowledged INTEGER NOT NULL DEFAULT 0, offered INTEGER NOT NULL DEFAULT 0, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(plugin_id,runtime_profile,consumer_id));
"""


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def submit_job(store, scope, plugin_id, operation, request_id, payload):
    encoded = encode(payload)
    with transaction(store._connect(), immediate=True) as db:
        row = db.execute('SELECT * FROM business_plugin_jobs WHERE plugin_id=? AND runtime_profile=? AND request_id=?',
                         (plugin_id, scope.runtime_profile, request_id)).fetchone()
        if row:
            if row['payload'] != encoded or row['operation'] != operation or tuple(row[k] for k in ('runtime_profile','transport_profile','account_id','contact_id')) != _scope_values(scope):
                raise BusinessConflict('request_payload_conflict')
            return dict(row)
        job_id = str(uuid.uuid4())
        db.execute('INSERT INTO business_plugin_jobs(job_id,plugin_id,runtime_profile,transport_profile,account_id,contact_id,request_id,operation,payload,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (job_id, plugin_id, *_scope_values(scope), request_id, operation, encoded, int(time.time()*1000)))
        return dict(db.execute('SELECT * FROM business_plugin_jobs WHERE job_id=?', (job_id,)).fetchone())


def record_contribution(store, scope, plugin_id, kind, payload):
    with transaction(store._connect(), immediate=True) as db:
        db.execute('INSERT INTO business_plugin_contributions(plugin_id,runtime_profile,transport_profile,account_id,contact_id,kind,item_id,payload) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(plugin_id,runtime_profile,transport_profile,account_id,contact_id,kind,item_id) DO UPDATE SET payload=excluded.payload',
                   (plugin_id, *_scope_values(scope), kind, payload['id'], encode(payload)))


def context_contributions(store, scope):
    with transaction(store._connect()) as db:
        rows = db.execute('SELECT kind,payload FROM business_plugin_contributions WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? ORDER BY plugin_id,item_id', _scope_values(scope)).fetchall()
    facts, actions = [], []
    for row in rows:
        item = json.loads(row['payload'])
        (facts if row['kind'] == 'fact' else actions).append((Fact if row['kind'] == 'fact' else ActionReceipt)(**item))
    return tuple(facts), tuple(actions)


def _refresh_outcomes(db, runtime_profile):
    rows = db.execute('SELECT * FROM business_outbox WHERE runtime_profile=?', (runtime_profile,)).fetchall()
    for row in rows:
        payload = {key:row[key] for key in ('runtime_profile','transport_profile','account_id','contact_id','outbox_id','revision_id','request_id','state','reason')}
        payload['id'] = f"outbox:{row['outbox_id']}:{row['state']}"
        job = db.execute('SELECT job_id,plugin_id,payload FROM business_plugin_jobs WHERE revision_id=?', (row['revision_id'],)).fetchone()
        if job:
            payload['job_id'] = job['job_id']
            payload['plugin_id'] = job['plugin_id']
            payload['job'] = json.loads(job['payload'])
        plan = db.execute('SELECT payload FROM business_plans WHERE plan_id=?',(row['plan_id'],)).fetchone()
        parts = json.loads(plan['payload']).get('parts',[]) if plan else []
        payload['text'] = '\n'.join(part.get('payload',{}).get('text','') for part in parts if part.get('operation') == 'text')
        payload['message_id'] = None
        attempts = db.execute('SELECT platform_ids FROM business_attempts WHERE outbox_id=?', (row['outbox_id'],)).fetchall()
        payload['message_ids'] = [mid for attempt in attempts for mid in json.loads(attempt['platform_ids'])]
        db.execute('INSERT OR IGNORE INTO business_plugin_outcomes(runtime_profile,transport_profile,account_id,contact_id,event_key,payload) VALUES(?,?,?,?,?,?)',
                   (runtime_profile, row['transport_profile'], row['account_id'], row['contact_id'], payload['id'], encode(payload)))


def outcome_events(store, principal, consumer_id, *, acknowledge=None):
    key = (principal.subject, principal.runtime_profile, consumer_id)
    with transaction(store._connect(), immediate=True) as db:
        _refresh_outcomes(db, principal.runtime_profile)
        db.execute('INSERT OR IGNORE INTO business_plugin_cursors(plugin_id,runtime_profile,consumer_id) VALUES(?,?,?)', key)
        cursor = db.execute('SELECT * FROM business_plugin_cursors WHERE plugin_id=? AND runtime_profile=? AND consumer_id=?', key).fetchone()
        if acknowledge is not None:
            if type(acknowledge) is not int or not cursor['acknowledged'] <= acknowledge <= cursor['offered']:
                raise BusinessConflict('invalid_cursor')
            db.execute('UPDATE business_plugin_cursors SET acknowledged=? WHERE plugin_id=? AND runtime_profile=? AND consumer_id=?', (acknowledge,*key))
            return {'ok':True, 'acknowledged':acknowledge}
        rows = db.execute('SELECT cursor,payload FROM business_plugin_outcomes WHERE runtime_profile=? AND cursor>? ORDER BY cursor', (principal.runtime_profile,cursor['acknowledged'])).fetchall()
        next_cursor = rows[-1]['cursor'] if rows else cursor['acknowledged']
        db.execute('UPDATE business_plugin_cursors SET offered=? WHERE plugin_id=? AND runtime_profile=? AND consumer_id=?', (next_cursor,*key))
        return {'ok':True,'events':[dict(json.loads(row['payload']),cursor=row['cursor']) for row in rows], 'next_cursor':next_cursor, 'acknowledged_cursor':cursor['acknowledged']}
