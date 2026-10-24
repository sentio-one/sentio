"""Snapshots, immutable revisions, and deduplicated task records."""

import json
import uuid
from dataclasses import asdict

from hermes_cli.sqlite_util import transaction
from gateway.business.models import (
    ActionReceipt, BusinessConflict, BusinessDenied, Candidate, CheckReport,
    ContextEvent, ContextSnapshot, DraftRevision, EpisodeRef, Fact, ReviewVerdict,
)
from gateway.business.store_episodes import _scope_values


def _json(value):
    return json.dumps(asdict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _scope(value):
    from gateway.business.models import Scope
    return Scope(**value)


def _snapshot(value):
    return ContextSnapshot(
        id=value["id"], scope=_scope(value["scope"]), episode=EpisodeRef(**value["episode"]),
        policy_version=value["policy_version"], lesson_version=value["lesson_version"],
        events=tuple(ContextEvent(**item) for item in value["events"]),
        facts=tuple(Fact(**item) for item in value["facts"]),
        actions=tuple(ActionReceipt(**item) for item in value["actions"]),
        rules=tuple(value["rules"]), review_criteria=tuple(value["review_criteria"]),
        confirmed_lessons=tuple(value["confirmed_lessons"]), missing=tuple(value["missing"]))


def _revision(value):
    candidate = Candidate(**value["candidate"])
    checks = CheckReport(**value["checks"]) if value["checks"] is not None else None
    review = ReviewVerdict(**value["review"]) if value["review"] is not None else None
    return DraftRevision(value["id"], _scope(value["scope"]), EpisodeRef(**value["episode"]),
                         value["number"], candidate, value["policy_version"], value["lesson_version"], checks, review)


def save_snapshot(store, snapshot):
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        owned = db.execute("SELECT 1 FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                           (*_scope_values(snapshot.scope), snapshot.episode.id)).fetchone()
        if not owned:
            raise BusinessDenied()
        db.execute("INSERT INTO business_contexts(runtime_profile,transport_profile,account_id,contact_id,snapshot_id,episode_id,payload) VALUES(?,?,?,?,?,?,?)",
                   (*_scope_values(snapshot.scope), snapshot.id, snapshot.episode.id, _json(snapshot)))


def get_snapshot(store, scope, snapshot_id):
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute("SELECT payload FROM business_contexts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND snapshot_id=?",
                         (*_scope_values(scope), snapshot_id)).fetchone()
    if not row:
        raise BusinessDenied()
    return _snapshot(json.loads(row[0]))


def append_revision(store, scope, episode, candidate, *, expected_revision_id, policy_version, lesson_version):
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        owned = db.execute("SELECT 1 FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                           (*_scope_values(scope), episode.id)).fetchone()
        if not owned:
            raise BusinessDenied()
        row = db.execute("SELECT revision_id,number FROM business_revisions WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=? ORDER BY number DESC LIMIT 1",
                         (*_scope_values(scope), episode.id)).fetchone()
        latest = row["revision_id"] if row else None
        if latest != expected_revision_id:
            raise BusinessConflict("revision_conflict")
        number = row["number"] + 1 if row else 1
        revision = DraftRevision(str(uuid.uuid4()), scope, episode, number, candidate, policy_version, lesson_version, None, None)
        db.execute("INSERT INTO business_revisions(runtime_profile,transport_profile,account_id,contact_id,revision_id,episode_id,number,parent_revision_id,payload) VALUES(?,?,?,?,?,?,?,?,?)",
                   (*_scope_values(scope), revision.id, episode.id, number, latest, _json(revision)))
        return revision


def get_revision(store, scope, revision_id):
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute("SELECT payload FROM business_revisions WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
                         (*_scope_values(scope), revision_id)).fetchone()
    if not row:
        raise BusinessDenied()
    return _revision(json.loads(row[0]))


def put_task(store, scope, *, dedupe_key, reason, refs):
    task_id = str(uuid.uuid4())
    payload = json.dumps({"reason": reason, "refs": list(refs)}, ensure_ascii=False, sort_keys=True)
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        row = db.execute("SELECT task_id,payload FROM business_tasks WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND dedupe_key=?",
                         (*_scope_values(scope), dedupe_key)).fetchone()
        if row:
            if row["payload"] != payload:
                raise BusinessConflict("task_payload_conflict")
            return row["task_id"]
        db.execute("INSERT INTO business_tasks(runtime_profile,transport_profile,account_id,contact_id,task_id,dedupe_key,payload) VALUES(?,?,?,?,?,?,?)",
                   (*_scope_values(scope), task_id, dedupe_key, payload))
        return task_id
