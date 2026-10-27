"""Ingress, contact ownership, and event history operations."""

import json
import uuid

from hermes_cli.sqlite_util import transaction
from gateway.business.models import BusinessConflict, BusinessDenied, EpisodeRef, Receipt, Scope


def _scope_values(scope):
    return (scope.runtime_profile, scope.transport_profile, scope.account_id, scope.contact_id)


def _event_payload(event):
    return json.dumps(event.__dict__, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def bind_contact(store, runtime_profile, transport_profile, account_id, identifiers):
    aliases = {_alias_key(value): value for value in identifiers if value}
    if not aliases:
        raise BusinessConflict("contact_identifier_required")
    conn = store._connect()
    collision = False
    with transaction(conn, immediate=True) as db:
        db.execute("INSERT OR IGNORE INTO business_accounts(runtime_profile,transport_profile,account_id) VALUES(?,?,?)",
                   (runtime_profile, transport_profile, account_id))
        owners = set()
        for alias in aliases:
            rows = db.execute("SELECT contact_id FROM business_aliases WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND alias=?",
                              (runtime_profile, transport_profile, account_id, alias)).fetchall()
            owners.update(row[0] for row in rows)
        if len(owners) > 1:
            collision = True
            for owner in owners:
                db.execute("UPDATE business_contacts SET held=1 WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                           (runtime_profile, transport_profile, account_id, owner))
                _put_collision_task(db, runtime_profile, transport_profile, account_id, owner, aliases)
            for alias in aliases:
                db.execute("UPDATE business_aliases SET held=1 WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND alias=?",
                           (runtime_profile, transport_profile, account_id, alias))
            contact_id = ""
        else:
            contact_id = next(iter(owners)) if owners else str(uuid.uuid4())
        if not collision:
            db.execute("INSERT OR IGNORE INTO business_contacts(runtime_profile,transport_profile,account_id,contact_id) VALUES(?,?,?,?)",
                       (runtime_profile, transport_profile, account_id, contact_id))
            for alias, raw in aliases.items():
                db.execute("INSERT OR IGNORE INTO business_aliases(runtime_profile,transport_profile,account_id,alias,transport_id,contact_id) VALUES(?,?,?,?,?,?)",
                           (runtime_profile, transport_profile, account_id, alias, raw, contact_id))
    if collision:
        raise BusinessConflict("alias_collision")
    return Scope(runtime_profile, transport_profile, account_id, contact_id)


def _alias_key(identifier):
    value = identifier.strip()
    if "@" not in value:
        return "phone:" + value.removeprefix("+")
    user, _, domain = value.partition("@")
    user = user.split(":", 1)[0]
    if domain == "s.whatsapp.net":
        return "phone:" + user.removeprefix("+")
    return f"{domain}:{user}"


def _put_collision_task(db, runtime_profile, transport_profile, account_id, contact_id, aliases):
    key = "alias-collision:" + ":".join(sorted(aliases))
    task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{runtime_profile}:{transport_profile}:{account_id}:{contact_id}:{key}"))
    payload = json.dumps({"reason": "alias_collision", "refs": sorted(aliases)}, sort_keys=True)
    db.execute("INSERT OR IGNORE INTO business_tasks(runtime_profile,transport_profile,account_id,contact_id,task_id,dedupe_key,payload) VALUES(?,?,?,?,?,?,?)",
               (runtime_profile, transport_profile, account_id, contact_id, task_id, key, payload))


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
        db.execute("UPDATE business_contacts SET context_version=? WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                   (version, *_scope_values(scope)))
        db.execute("UPDATE business_revisions SET status='superseded' WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND status='pending'",
                   _scope_values(scope))
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
        row = db.execute("SELECT state FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                         (*_scope_values(scope), episode_id)).fetchone()
        if not row:
            raise BusinessDenied()
        verdict_key = json.dumps([episode_id, kind, reason], separators=(",", ":"))
        old = db.execute("SELECT receipt_id,state FROM business_verdicts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND verdict_key=?",
                         (*_scope_values(scope), verdict_key)).fetchone()
        if old:
            return Receipt(old["receipt_id"], kind, None, reason)
        receipt_id = str(uuid.uuid4())
        state = "resolved" if kind in ("no_reply", "refused") else "owed"
        db.execute("INSERT INTO business_verdicts(runtime_profile,transport_profile,account_id,contact_id,episode_id,verdict_key,receipt_id,kind,reason,state) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (*_scope_values(scope), episode_id, verdict_key, receipt_id, kind, reason, state))
        db.execute("UPDATE business_episodes SET state=? WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                   (state, *_scope_values(scope), episode_id))
        return Receipt(receipt_id, kind, None, reason)


def episode_for_source(store, scope, source_id):
    """The episode a prior ``record_ingress`` created for *source_id*; BusinessDenied when none is owned."""
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute("SELECT i.episode_id, e.context_version FROM business_ingress i JOIN business_episodes e "
                         "ON e.runtime_profile=i.runtime_profile AND e.transport_profile=i.transport_profile "
                         "AND e.account_id=i.account_id AND e.contact_id=i.contact_id AND e.episode_id=i.episode_id "
                         "WHERE i.runtime_profile=? AND i.transport_profile=? AND i.account_id=? AND i.contact_id=? "
                         "AND i.source_id=?", (*_scope_values(scope), source_id)).fetchone()
    if not row:
        raise BusinessDenied()
    return EpisodeRef(row[0], row[1])


def get_verdict(store, scope, episode_id):
    from gateway.business.models import VerdictRecord
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute("SELECT episode_id, receipt_id, kind, reason, state FROM business_verdicts "
                         "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                         (*_scope_values(scope), episode_id)).fetchone()
    if not row:
        raise BusinessDenied()
    return VerdictRecord(episode_id=row["episode_id"], verdict=row["kind"], reason=row["reason"],
                         state=row["state"], receipt_id=row["receipt_id"])


def list_verdicts(store, scope):
    from gateway.business.models import VerdictRecord
    conn = store._connect()
    with transaction(conn) as db:
        rows = db.execute("SELECT episode_id, receipt_id, kind, reason, state FROM business_verdicts "
                          "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? "
                          "ORDER BY receipt_id", _scope_values(scope)).fetchall()
    return tuple(VerdictRecord(episode_id=row["episode_id"], verdict=row["kind"], reason=row["reason"],
                               state=row["state"], receipt_id=row["receipt_id"]) for row in rows)


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
        payload = json.dumps({"suppressed": True, "reason": reason}, sort_keys=True)
        previous = db.execute("SELECT kind,reason,payload FROM business_audit WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND source_id=?",
                             (*_scope_values(scope), source_id)).fetchone()
        if previous:
            if previous["kind"] != "suppression" or previous["reason"] != reason or previous["payload"] != payload:
                raise BusinessConflict("suppression_source_conflict")
            return
        db.execute("UPDATE business_contacts SET suppressed=1,suppression_reason=? WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                   (reason, *_scope_values(scope)))
        db.execute("INSERT INTO business_audit(runtime_profile,transport_profile,account_id,contact_id,source_id,kind,reason,payload) VALUES(?,?,?,?,?,?,?,?)",
                   (*_scope_values(scope), source_id, "suppression", reason, payload))


def is_suppressed(store, scope):
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute("SELECT suppressed FROM business_contacts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                         _scope_values(scope)).fetchone()
    return bool(row[0]) if row else False


def is_held(store, scope):
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute("SELECT held FROM business_contacts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                         _scope_values(scope)).fetchone()
    return bool(row[0]) if row else False


def contact_identifiers(store, scope):
    conn = store._connect()
    with transaction(conn) as db:
        rows = db.execute("SELECT transport_id FROM business_aliases WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? ORDER BY transport_id",
                          _scope_values(scope)).fetchall()
    return tuple(row[0] for row in rows)


class EpisodeState(str):
    @property
    def state(self) -> str:
        return str(self)


def episode_status(store, scope, episode_id):
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute("SELECT state FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                         (*_scope_values(scope), episode_id)).fetchone()
    if not row:
        raise BusinessDenied()
    return EpisodeState(row[0])


def audit_events(store, scope):
    conn = store._connect()
    with transaction(conn) as db:
        audits = db.execute("SELECT source_id,kind,reason,payload FROM business_audit WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                            _scope_values(scope)).fetchall()
        verdicts = db.execute("SELECT episode_id,receipt_id,kind,reason,state FROM business_verdicts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                              _scope_values(scope)).fetchall()
    rows = [dict(row) for row in audits]
    rows.extend({"source_id": f"verdict:{row['episode_id']}:{row['receipt_id']}", "kind": row["kind"],
                 "reason": row["reason"], "payload": json.dumps({"state": row["state"]}, sort_keys=True)}
                for row in verdicts)
    return tuple(sorted(rows, key=lambda row: row["source_id"]))
