"""Snapshots, immutable revisions, and deduplicated task records."""

import json
import uuid
import copy
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
    candidate_value = value["candidate"]
    candidate_value["data"] = copy.deepcopy(candidate_value["data"])
    for field in ("fact_ids", "completed_action_ids", "credential_slots"):
        candidate_value[field] = tuple(candidate_value[field])
    candidate = Candidate(**candidate_value)
    checks_value = value["checks"]
    if checks_value is not None:
        checks_value["issues"] = tuple(checks_value["issues"])
    checks = CheckReport(**checks_value) if checks_value is not None else None
    review_value = value["review"]
    if review_value is not None:
        review_value["reasons"] = tuple(review_value["reasons"])
        review_value["policy_refs"] = tuple(review_value["policy_refs"])
    review = ReviewVerdict(**review_value) if review_value is not None else None
    return DraftRevision(value["id"], _scope(value["scope"]), EpisodeRef(**value["episode"]),
                         value["number"], candidate, value["policy_version"], value["lesson_version"], checks, review)


def save_snapshot(store, snapshot):
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        owned = db.execute("SELECT context_version FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                           (*_scope_values(snapshot.scope), snapshot.episode.id)).fetchone()
        current = db.execute("SELECT context_version FROM business_contacts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                             _scope_values(snapshot.scope)).fetchone()
        if not owned or owned[0] != snapshot.episode.context_version or current[0] != snapshot.episode.context_version:
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
        current = db.execute("SELECT context_version,held FROM business_contacts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
                             _scope_values(scope)).fetchone()
        stored = db.execute("SELECT context_version,state FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
                            (*_scope_values(scope), episode.id)).fetchone()
        if current[1] or stored[0] != episode.context_version or current[0] != episode.context_version:
            raise BusinessConflict("stale_episode")
        row = db.execute("SELECT revision_id,number FROM business_revisions WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=? ORDER BY number DESC LIMIT 1",
                         (*_scope_values(scope), episode.id)).fetchone()
        latest = row["revision_id"] if row else None
        if latest != expected_revision_id:
            raise BusinessConflict("revision_conflict")
        number = row["number"] + 1 if row else 1
        stored_candidate = Candidate(
            candidate.kind, candidate.text, copy.deepcopy(candidate.data), tuple(candidate.fact_ids),
            tuple(candidate.completed_action_ids), tuple(candidate.credential_slots))
        revision = DraftRevision(str(uuid.uuid4()), scope, episode, number, stored_candidate, policy_version, lesson_version, None, None)
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


def list_revisions(store, scope, episode_id=None):
    conn = store._connect()
    with transaction(conn) as db:
        if episode_id is not None:
            rows = db.execute(
                "SELECT payload FROM business_revisions WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=? ORDER BY number ASC",
                (*_scope_values(scope), episode_id),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT payload FROM business_revisions WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? ORDER BY number ASC",
                _scope_values(scope),
            ).fetchall()
    return tuple(_revision(json.loads(row[0])) for row in rows)


def attach_checks(store, scope, revision_id, *, checks, expected_context_version):
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        owned = db.execute(
            "SELECT episode_id, number, parent_revision_id, payload FROM business_revisions "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*_scope_values(scope), revision_id),
        ).fetchone()
        if not owned:
            raise BusinessDenied()
        episode_id = owned["episode_id"]
        current = db.execute(
            "SELECT context_version, held FROM business_contacts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
            _scope_values(scope),
        ).fetchone()
        stored = db.execute(
            "SELECT context_version, state FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
            (*_scope_values(scope), episode_id),
        ).fetchone()
        if current[1] or stored[0] != expected_context_version or current[0] != expected_context_version:
            raise BusinessConflict("stale_episode")

        old_rev = _revision(json.loads(owned["payload"]))
        new_rev = DraftRevision(
            id=old_rev.id,
            scope=old_rev.scope,
            episode=old_rev.episode,
            number=old_rev.number,
            candidate=old_rev.candidate,
            policy_version=old_rev.policy_version,
            lesson_version=old_rev.lesson_version,
            checks=checks,
            review=old_rev.review,
        )
        db.execute(
            "UPDATE business_revisions SET payload=? WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (_json(new_rev), *_scope_values(scope), revision_id),
        )
        return new_rev


def attach_review(store, scope, revision_id, *, verdict, expected_context_version):
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        owned = db.execute(
            "SELECT episode_id, number, parent_revision_id, payload FROM business_revisions "
            "WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*_scope_values(scope), revision_id),
        ).fetchone()
        if not owned:
            raise BusinessDenied()
        episode_id = owned["episode_id"]
        current = db.execute(
            "SELECT context_version, held FROM business_contacts WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=?",
            _scope_values(scope),
        ).fetchone()
        stored = db.execute(
            "SELECT context_version, state FROM business_episodes WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND episode_id=?",
            (*_scope_values(scope), episode_id),
        ).fetchone()
        if current[1] or stored[0] != expected_context_version or current[0] != expected_context_version:
            raise BusinessConflict("stale_episode")

        old_rev = _revision(json.loads(owned["payload"]))
        new_rev = DraftRevision(
            id=old_rev.id,
            scope=old_rev.scope,
            episode=old_rev.episode,
            number=old_rev.number,
            candidate=old_rev.candidate,
            policy_version=old_rev.policy_version,
            lesson_version=old_rev.lesson_version,
            checks=old_rev.checks,
            review=verdict,
        )
        db.execute(
            "UPDATE business_revisions SET payload=? WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (_json(new_rev), *_scope_values(scope), revision_id),
        )
        return new_rev


class RevisionState(str):
    @property
    def state(self) -> str:
        return str(self)


def revision_status(store, scope, revision_id):
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute("SELECT status FROM business_revisions WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
                         (*_scope_values(scope), revision_id)).fetchone()
    if not row:
        raise BusinessDenied()
    return RevisionState(row[0])


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


def get_task(store, scope, task_id):
    from gateway.business.models import TaskRecord
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute("SELECT task_id,dedupe_key,payload FROM business_tasks WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND task_id=?",
                         (*_scope_values(scope), task_id)).fetchone()
    if not row:
        raise BusinessDenied()
    payload = json.loads(row["payload"])
    return TaskRecord(id=row["task_id"], dedupe_key=row["dedupe_key"], reason=payload.get("reason", ""),
                      refs=tuple(payload.get("refs", ())))


def list_tasks(store, scope):
    from gateway.business.models import TaskRecord
    conn = store._connect()
    with transaction(conn) as db:
        rows = db.execute("SELECT task_id,dedupe_key,payload FROM business_tasks WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? ORDER BY task_id",
                          _scope_values(scope)).fetchall()
    tasks = []
    for row in rows:
        payload = json.loads(row["payload"])
        tasks.append(TaskRecord(id=row["task_id"], dedupe_key=row["dedupe_key"], reason=payload.get("reason", ""),
                                refs=tuple(payload.get("refs", ()))))
    return tuple(tasks)


def _plan(value):
    from gateway.business.render import DeliveryPart, DeliveryPlan
    parts = tuple(DeliveryPart(**p) for p in value["parts"])
    return DeliveryPlan(
        id=value["id"],
        revision_id=value["revision_id"],
        scope=_scope(value["scope"]),
        context_version=value["context_version"],
        policy_version=value["policy_version"],
        parts=parts,
        expires_at_ms=value["expires_at_ms"],
        digest=value["digest"],
        encrypted_plan_handle=value.get("encrypted_plan_handle"),
    )


def save_plan(store, plan):
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        owned = db.execute(
            "SELECT episode_id FROM business_revisions WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*_scope_values(plan.scope), plan.revision_id),
        ).fetchone()
        if not owned:
            raise BusinessDenied()
        db.execute(
            "INSERT INTO business_plans(runtime_profile, transport_profile, account_id, contact_id, plan_id, revision_id, digest, payload, expires_at_ms) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                *_scope_values(plan.scope),
                plan.id,
                plan.revision_id,
                plan.digest,
                _json(plan),
                plan.expires_at_ms,
            ),
        )


def get_plan(store, scope, plan_id):
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute(
            "SELECT payload FROM business_plans WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND plan_id=?",
            (*_scope_values(scope), plan_id),
        ).fetchone()
    if not row:
        raise BusinessDenied()
    return _plan(json.loads(row[0]))


def get_plan_by_revision(store, scope, revision_id):
    conn = store._connect()
    with transaction(conn) as db:
        row = db.execute(
            "SELECT payload FROM business_plans WHERE runtime_profile=? AND transport_profile=? AND account_id=? AND contact_id=? AND revision_id=?",
            (*_scope_values(scope), revision_id),
        ).fetchone()
    if not row:
        return None
    return _plan(json.loads(row[0]))

