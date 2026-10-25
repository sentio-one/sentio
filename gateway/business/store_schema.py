"""Versioned schema for durable, profile-owned business records."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS business_accounts (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 schema_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(runtime_profile, transport_profile, account_id));
CREATE TABLE IF NOT EXISTS business_contacts (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, suppressed INTEGER NOT NULL DEFAULT 0, suppression_reason TEXT,
 held INTEGER NOT NULL DEFAULT 0, context_version INTEGER NOT NULL DEFAULT 0,
 schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id));
CREATE TABLE IF NOT EXISTS business_aliases (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 alias TEXT NOT NULL, transport_id TEXT NOT NULL, contact_id TEXT NOT NULL, held INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, alias, transport_id, contact_id));
CREATE TABLE IF NOT EXISTS business_ingress (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, source_id TEXT NOT NULL, payload TEXT NOT NULL,
 episode_id TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, source_id));
CREATE TABLE IF NOT EXISTS business_episodes (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, episode_id TEXT NOT NULL, context_version INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'open', schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id, episode_id));
CREATE TABLE IF NOT EXISTS business_contexts (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, episode_id TEXT NOT NULL,
 payload TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id, snapshot_id));
CREATE TABLE IF NOT EXISTS business_revisions (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, revision_id TEXT NOT NULL, episode_id TEXT NOT NULL,
 number INTEGER NOT NULL, parent_revision_id TEXT, payload TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id, revision_id),
 UNIQUE(runtime_profile, transport_profile, account_id, contact_id, episode_id, number));
CREATE TABLE IF NOT EXISTS business_tasks (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, task_id TEXT NOT NULL, dedupe_key TEXT NOT NULL, payload TEXT NOT NULL,
 schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id, task_id),
 UNIQUE(runtime_profile, transport_profile, account_id, contact_id, dedupe_key));
CREATE TABLE IF NOT EXISTS business_events (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL,
 schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id, ordinal));
CREATE TABLE IF NOT EXISTS business_verdicts (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, episode_id TEXT NOT NULL, verdict_key TEXT NOT NULL,
 receipt_id TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL,
 state TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id, verdict_key));
CREATE TABLE IF NOT EXISTS business_audit (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, source_id TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL,
 payload TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id, source_id));
CREATE TABLE IF NOT EXISTS business_plans (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, plan_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 digest TEXT NOT NULL, payload TEXT NOT NULL, expires_at_ms INTEGER NOT NULL,
 schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id, plan_id),
 UNIQUE(runtime_profile, transport_profile, account_id, contact_id, revision_id));
CREATE TABLE IF NOT EXISTS business_approvals (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, approval_id TEXT NOT NULL, plan_id TEXT NOT NULL,
 revision_id TEXT NOT NULL, actor TEXT NOT NULL, plan_digest TEXT NOT NULL,
 approved_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL, override_reason TEXT,
 payload TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id, approval_id),
 UNIQUE(runtime_profile, transport_profile, account_id, contact_id, revision_id, actor, plan_digest));
CREATE TABLE IF NOT EXISTS business_outbox (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, outbox_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 plan_id TEXT NOT NULL, approval_id TEXT, request_id TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'held', created_at_ms INTEGER NOT NULL,
 expires_at_ms INTEGER NOT NULL, reason TEXT, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id, outbox_id),
 UNIQUE(runtime_profile, transport_profile, account_id, contact_id, request_id));
CREATE TABLE IF NOT EXISTS business_attempts (
 attempt_id TEXT NOT NULL, outbox_id TEXT NOT NULL, transport_profile TEXT NOT NULL,
 account_id TEXT NOT NULL, part_index INTEGER NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
 state TEXT NOT NULL DEFAULT 'claimed', platform_ids TEXT NOT NULL DEFAULT '[]',
 error_code TEXT, claimed_at_ms INTEGER NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(attempt_id),
 UNIQUE(outbox_id, part_index, generation));
CREATE TABLE IF NOT EXISTS business_governor_accounts (
 transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 paused INTEGER NOT NULL DEFAULT 0, pause_reason TEXT,
 high_watermark_ms INTEGER NOT NULL DEFAULT 0, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(transport_profile, account_id));
CREATE TABLE IF NOT EXISTS business_governor_reservations (
 transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL, message_class TEXT NOT NULL,
 reserved_at_ms INTEGER NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(transport_profile, account_id, attempt_id));
CREATE TABLE IF NOT EXISTS business_governor_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, transport_profile TEXT NOT NULL,
 account_id TEXT NOT NULL, message_class TEXT NOT NULL,
 timestamp_ms INTEGER NOT NULL, success INTEGER NOT NULL DEFAULT 1,
 schema_version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS idx_business_governor_attempts_ts
 ON business_governor_attempts(transport_profile, account_id, timestamp_ms);
CREATE TABLE IF NOT EXISTS business_consumed_authorizations (
 token_hash TEXT PRIMARY KEY, attempt_id TEXT NOT NULL,
 consumed_at_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS business_delivery_receipts (
 transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 message_id TEXT NOT NULL, status INTEGER NOT NULL,
 status_name TEXT NOT NULL, state TEXT NOT NULL,
 timestamp_ms INTEGER NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(transport_profile, account_id, message_id));
CREATE TABLE IF NOT EXISTS business_account_modes (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 mode TEXT NOT NULL DEFAULT 'off', updated_at_ms INTEGER NOT NULL,
 PRIMARY KEY(runtime_profile, transport_profile, account_id));
CREATE TABLE IF NOT EXISTS business_bridge_states (
 transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 state TEXT NOT NULL, code TEXT, respawn_count INTEGER NOT NULL DEFAULT 0,
 updated_at_ms INTEGER NOT NULL,
 PRIMARY KEY(transport_profile, account_id));
CREATE TABLE IF NOT EXISTS business_digests (
 digest_id TEXT PRIMARY KEY, runtime_profile TEXT NOT NULL,
 transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, revision_ids TEXT NOT NULL,
 created_at_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS business_lessons (
 lesson_id TEXT PRIMARY KEY, runtime_profile TEXT NOT NULL,
 transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 policy_id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 text TEXT NOT NULL, evidence_revision_ids TEXT NOT NULL,
 state TEXT NOT NULL, revoke_reason TEXT,
 created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS business_reviews (
 review_id TEXT PRIMARY KEY, runtime_profile TEXT NOT NULL,
 transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 policy_version TEXT NOT NULL, model_version TEXT NOT NULL,
 message_class TEXT NOT NULL, outcome TEXT NOT NULL,
 agreed INTEGER NOT NULL DEFAULT 1, false_approval INTEGER NOT NULL DEFAULT 0,
 unavailable INTEGER NOT NULL DEFAULT 0, unpaired INTEGER NOT NULL DEFAULT 0,
 reviewed_at_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS business_legacy_imports (
 source_digest TEXT NOT NULL, legacy_id TEXT NOT NULL,
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL,
 account_id TEXT NOT NULL, contact_id TEXT NOT NULL,
 outbox_id TEXT, imported_at_ms INTEGER NOT NULL,
 PRIMARY KEY(source_digest, legacy_id, runtime_profile, transport_profile, account_id, contact_id));
"""


def initialize_schema(conn):
    from hermes_cli.sqlite_util import add_column_if_missing

    conn.executescript(SCHEMA_SQL)
    from gateway.business.store_jobs import SCHEMA_SQL as PLUGIN_SCHEMA_SQL
    conn.executescript(PLUGIN_SCHEMA_SQL)
    add_column_if_missing(conn, "business_contacts", "held", "held INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "business_contacts", "context_version", "context_version INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "business_aliases", "transport_id", "transport_id TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(conn, "business_revisions", "status", "status TEXT NOT NULL DEFAULT 'pending'")
    add_column_if_missing(conn, "business_tasks", "resolved_at_ms", "resolved_at_ms INTEGER")
    add_column_if_missing(conn, "business_tasks", "resolution", "resolution TEXT")
    add_column_if_missing(conn, "business_tasks", "resolved_by", "resolved_by TEXT")
    # Legacy rows predate classification; the strictest class is the safe default.
    add_column_if_missing(conn, "business_outbox", "message_class", "message_class TEXT NOT NULL DEFAULT 'cold'")
    add_column_if_missing(conn, "business_digests", "plan_digests", "plan_digests TEXT NOT NULL DEFAULT '{}'")
    conn.execute("""CREATE TABLE IF NOT EXISTS business_credential_grants (
      grant_id TEXT PRIMARY KEY, runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL,
      account_id TEXT NOT NULL, contact_id TEXT NOT NULL, payload TEXT NOT NULL,
      created_at_ms INTEGER NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS business_draft_measurements (
      runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
      revision_id TEXT NOT NULL PRIMARY KEY, elapsed_ms REAL NOT NULL,
      schema_version INTEGER NOT NULL DEFAULT 1)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS business_operator_cards (
      message_id TEXT PRIMARY KEY, runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL,
      account_id TEXT NOT NULL, contact_id TEXT NOT NULL, digest_id TEXT NOT NULL,
      revision_id TEXT NOT NULL, plan_digest TEXT NOT NULL, created_at_ms INTEGER NOT NULL)""")
