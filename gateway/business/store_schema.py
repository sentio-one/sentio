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
"""


def initialize_schema(conn):
    from hermes_cli.sqlite_util import add_column_if_missing

    conn.executescript(SCHEMA_SQL)
    add_column_if_missing(conn, "business_contacts", "held", "held INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "business_contacts", "context_version", "context_version INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "business_aliases", "transport_id", "transport_id TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(conn, "business_revisions", "status", "status TEXT NOT NULL DEFAULT 'pending'")
