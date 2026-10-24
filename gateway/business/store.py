"""Profile-scoped durable business store facade."""

from pathlib import Path

from hermes_cli.sqlite_util import open_db
from gateway.business.store_schema import initialize_schema


class BusinessStore:
    def __init__(self, launch_home: Path):
        self.db_path = Path(launch_home) / "state.db"
        self._initialize()

    def _connect(self):
        return open_db(self.db_path, db_label="business", synchronous_full=True,
                       initialize=initialize_schema)

    def _initialize(self):
        conn = self._connect()
        conn.close()

    def bind_contact(self, runtime_profile, transport_profile, account_id, identifiers):
        from gateway.business.store_episodes import bind_contact
        return bind_contact(self, runtime_profile, transport_profile, account_id, identifiers)

    def record_ingress(self, scope, event, *, source_id):
        from gateway.business.store_episodes import record_ingress
        return record_ingress(self, scope, event, source_id=source_id)

    def record_verdict(self, scope, episode_id, *, kind, reason):
        from gateway.business.store_episodes import record_verdict
        return record_verdict(self, scope, episode_id, kind=kind, reason=reason)

    def get_verdict(self, scope, episode_id):
        from gateway.business.store_episodes import get_verdict
        return get_verdict(self, scope, episode_id)

    def list_verdicts(self, scope):
        from gateway.business.store_episodes import list_verdicts
        return list_verdicts(self, scope)

    def episode_for_source(self, scope, source_id):
        from gateway.business.store_episodes import episode_for_source
        return episode_for_source(self, scope, source_id)

    def list_events(self, scope):
        from gateway.business.store_episodes import list_events
        return list_events(self, scope)

    def set_suppressed(self, scope, *, reason, source_id):
        from gateway.business.store_episodes import set_suppressed
        return set_suppressed(self, scope, reason=reason, source_id=source_id)

    def is_suppressed(self, scope):
        from gateway.business.store_episodes import is_suppressed
        return is_suppressed(self, scope)

    def is_held(self, scope):
        from gateway.business.store_episodes import is_held
        return is_held(self, scope)

    def contact_identifiers(self, scope):
        from gateway.business.store_episodes import contact_identifiers
        return contact_identifiers(self, scope)

    def episode_status(self, scope, episode_id):
        from gateway.business.store_episodes import episode_status
        return episode_status(self, scope, episode_id)

    def audit_events(self, scope):
        from gateway.business.store_episodes import audit_events
        return audit_events(self, scope)

    def save_snapshot(self, snapshot):
        from gateway.business.store_drafts import save_snapshot
        return save_snapshot(self, snapshot)

    def get_snapshot(self, scope, snapshot_id):
        from gateway.business.store_drafts import get_snapshot
        return get_snapshot(self, scope, snapshot_id)

    def append_revision(self, scope, episode, candidate, *, expected_revision_id, policy_version, lesson_version):
        from gateway.business.store_drafts import append_revision
        return append_revision(self, scope, episode, candidate, expected_revision_id=expected_revision_id,
                               policy_version=policy_version, lesson_version=lesson_version)

    def get_revision(self, scope, revision_id):
        from gateway.business.store_drafts import get_revision
        return get_revision(self, scope, revision_id)

    def list_revisions(self, scope, episode_id=None):
        from gateway.business.store_drafts import list_revisions
        return list_revisions(self, scope, episode_id)

    def attach_checks(self, scope, revision_id, *, checks, expected_context_version):
        from gateway.business.store_drafts import attach_checks
        return attach_checks(self, scope, revision_id, checks=checks, expected_context_version=expected_context_version)

    def attach_review(self, scope, revision_id, *, verdict, expected_context_version):
        from gateway.business.store_drafts import attach_review
        return attach_review(self, scope, revision_id, verdict=verdict, expected_context_version=expected_context_version)

    def put_task(self, scope, *, dedupe_key, reason, refs):
        from gateway.business.store_drafts import put_task
        return put_task(self, scope, dedupe_key=dedupe_key, reason=reason, refs=refs)

    def get_task(self, scope, task_id):
        from gateway.business.store_drafts import get_task
        return get_task(self, scope, task_id)

    def list_tasks(self, scope):
        from gateway.business.store_drafts import list_tasks
        return list_tasks(self, scope)

    def revision_status(self, scope, revision_id):
        from gateway.business.store_drafts import revision_status
        return revision_status(self, scope, revision_id)
