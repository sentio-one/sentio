"""Versioned schema for durable, profile-owned business records."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS business_accounts (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 schema_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(runtime_profile, transport_profile, account_id));
CREATE TABLE IF NOT EXISTS business_contacts (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 contact_id TEXT NOT NULL, suppressed INTEGER NOT NULL DEFAULT 0, suppression_reason TEXT,
 schema_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, contact_id));
CREATE TABLE IF NOT EXISTS business_aliases (
 runtime_profile TEXT NOT NULL, transport_profile TEXT NOT NULL, account_id TEXT NOT NULL,
 alias TEXT NOT NULL, contact_id TEXT NOT NULL, held INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(runtime_profile, transport_profile, account_id, alias, contact_id));
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
"""
