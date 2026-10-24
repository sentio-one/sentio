"""Ingress, contact ownership, and event history operations."""

import json
import sqlite3
import uuid

from hermes_cli.sqlite_util import transaction
from gateway.business.models import BusinessConflict, BusinessDenied, EpisodeRef, Receipt, Scope


def _scope_values(scope):
    return (scope.runtime_profile, scope.transport_profile, scope.account_id, scope.contact_id)


def _event_payload(event):
    return json.dumps(event.__dict__, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def bind_contact(store, runtime_profile, transport_profile, account_id, identifiers):
    from gateway.whatsapp_identity import normalize_whatsapp_identifier
    aliases = {normalize_whatsapp_identifier(value) for value in identifiers}
    aliases.discard("")
    if not aliases:
        aliases = {"unbound:" + uuid.uuid4().hex}
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        db.execute("INSERT OR IGNORE INTO business_accounts(runtime_profile,transport_profile,account_id) VALUES(?,?,?)",
                   (runtime_profile, transport_profile, account_id))
        owners = set()
        for alias in aliases:
            rows = db.execute("SELECT contact_id FROM business_aliases WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND alias=?",
                              (runtime_profile, transport_profile, account_id, alias)).fetchall()
            owners.update(row[0] for row in rows)
        contact_id = next(iter(owners)) if len(owners) == 1 else str(uuid.uuid4())
        if len(owners) > 1:
            for alias in aliases:
                db.execute("UPDATE business_aliases SET held=1 WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND alias=?",
                           (runtime_profile, transport_profile, account_id, alias))
        db.execute("INSERT OR IGNORE INTO business_contacts(runtime_profile,transport_profile,account_id,contact_id) VALUES(?,?,?,?)",
                   (runtime_profile, transport_profile, account_id, contact_id))
        for alias in aliases:
            rows = db.execute("SELECT contact_id FROM business_aliases WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND alias=?",
                              (runtime_profile, transport_profile, account_id, alias)).fetchall()
            db.execute("INSERT OR IGNORE INTO business_aliases(runtime_profile,transport_profile,account_id,alias,contact_id,held) VALUES(?,?,?,?,?,?)",
                       (runtime_profile, transport_profile, account_id, alias, contact_id, int(bool(rows) and contact_id not in {row[0] for row in rows})))
    return Scope(runtime_profile, transport_profile, account_id, contact_id)


def _ensure_contact(db, scope):
    db.execute("INSERT OR IGNORE INTO business_accounts(runtime_profile,transport_profile,account_id) VALUES(?,?,?)",
               (scope.runtime_profile, scope.transport_profile, scope.account_id))
    db.execute("INSERT OR IGNORE INTO business_contacts(runtime_profile,transport_profile,account_id,contact_id) VALUES(?,?,?,?)",
               _scope_values(scope))


def record_ingress(store, scope, event, *, source_id):
    payload = _event_payload(event)
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        _ensure_contact(db, scope)
        existing = db.execute("SELECT payload,episode_id,contact_id FROM business_ingress WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND source_id=?",
                              (scope.runtime_profile, scope.transport_profile, scope.account_id, source_id)).fetchone()
        if existing:
            if existing["contact_id"] != scope.contact_id:
                raise BusinessDenied()
            if existing["payload"] != payload:
                raise BusinessConflict("source_payload_conflict")
            episode = db.execute("SELECT context_version FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                                 (*_scope_values(scope), existing["episode_id"])).fetchone()
            return EpisodeRef(existing["episode_id"], episode[0])
        prior = db.execute("SELECT COALESCE(MAX(context_version),0) FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                           _scope_values(scope)).fetchone()[0]
        episode_id = str(uuid.uuid4())
        version = prior + 1
        db.execute("INSERT INTO business_episodes(runtime_profile,transport_profile,account_id,contact_id,episode_id,context_version) VALUES(?,?,?,?,?,?)",
                   (*_scope_values(scope), episode_id, version))
        db.execute("INSERT INTO business_ingress(runtime_profile,transport_profile,account_id,contact_id,source_id,payload,episode_id) VALUES(?,?,?,?,?,?,?)",
                   (*_scope_values(scope), source_id, payload, episode_id))
        ordinal = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM business_events WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                             _scope_values(scope)).fetchone()[0]
        db.execute("INSERT INTO business_events(runtime_profile,transport_profile,account_id,contact_id,ordinal,payload) VALUES(?,?,?,?,?,?)",
                   (*_scope_values(scope), ordinal, payload))
        return EpisodeRef(episode_id, version)


def record_verdict(store, scope, episode_id, *, kind, reason):
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        _ensure_contact(db, scope)
        row = db.execute("SELECT 1 FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                         (*_scope_values(scope), episode_id)).fetchone()
        if not row:
            raise BusinessDenied()
        receipt_id = str(uuid.uuid4())
        payload = json.dumps({"kind": kind, "reason": reason, "receipt_id": receipt_id}, sort_keys=True)
        ordinal = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM business_events WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                             _scope_values(scope)).fetchone()[0]
        db.execute("INSERT INTO business_events(runtime_profile,transport_profile,account_id,contact_id,ordinal,payload) VALUES(?,?,?,?,?,?)",
                   (*_scope_values(scope), ordinal, payload))
        return Receipt(receipt_id, kind, None, reason)


def list_events(store, scope):
    from gateway.business.models import ContextEvent
    conn = store._connect()
    with transaction(conn) as db:
        rows = db.execute("SELECT payload FROM business_events WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? ORDER BY ordinal",
                          _scope_values(scope)).fetchall()
    events = []
    for row in rows:
        value = json.loads(row[0])
        if set(value) == {"id", "kind", "actor", "text", "target_id", "evidence"}:
            events.append(ContextEvent(**value))
    return tuple(events)


def set_suppressed(store, scope, *, reason, source_id):
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        _ensure_contact(db, scope)
        db.execute("UPDATE business_contacts SET suppressed=1,suppression_reason=? WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                   (reason, *_scope_values(scope)))


def is_suppressed(store, scope):
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute("SELECT suppressed FROM business_contacts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                         _scope_values(scope)).fetchone()
    return bool(row[0]) if row else False
