# Native training wheels integration repairs

**Goal:** Resolve every finding in WHATSAPP_TRAINING_WHEELS_REVIEW.md against the
approved WHATSAPP_TRAINING_WHEELS_DESIGN.md, with real runtime integration tests.
The user authorized implementation of all findings on 2026-10-25.

**Constraints:** Preserve personal/shadow behavior, profile ownership, stable
prompt prefixes, mandatory human approval, two rewrites maximum, no automatic
resend after uncertainty, and no live cutover/sending. Use additive SQLite schema,
existing auxiliary routing and profile scopes, and `scripts/run_tests.sh`.
Keep unrelated `.orca/` and `package-lock.json` changes out of commits.

Independent repair domains may execute concurrently with exclusive file ownership;
the root integrates their shared contracts and runs the final complete workflow.

## 1. Drafting and startup (root)

Own `lifecycle.py`, `service.py`, `turns.py`, `decision.py`, `compose.py`,
`review.py`, `context.py`, `policy.py`, `readiness.py`, `workflow_readiness.py`,
and new startup/drafting tests. Fix R1/R3 and draft promotion in R4; supply full
context and honest readiness/metrics. Startup retains a held service on invalid
or unlatched configuration; the latch is account/profile bound. Control server
registration uses `session_control_server` and the owning event loop. Start/stop
the transport worker through its exported lifecycle helpers.

Write failing startup/result/promotion/context/readiness tests, verify red, repair,
and verify green. Rewrite fake full acceptance and egress tests after other domains
are integrated. Confirm new inbound invalidates approvals and no checked candidate
is silently discarded.

## 2. Native physical transport and receipts

Own `transport.py`, `dispatch.py`, `store_outbox.py`, `store_governor.py`,
`receipts.py`, `recovery.py`, adapter/bridge transport changes and their tests.
Fix R2/R7/R8 and the `CheckReport.ok` mismatch from R4. Add a worker module exporting
`start_dispatch_worker(runner, service)` and `stop_dispatch_worker(runner)`; the
root calls these from lifecycle. No edits to root-owned lifecycle/service files.
Bridge authorization uses the existing authenticated local control protocol;
all physical send endpoints fail closed for a latched business account. Preserve
exact rendered bytes and scope at consumption, one-use tokens, governor pacing,
and ambiguous-attempt recovery. Real receipt polling calls durable reconciliation
with canonical account identity rather than creating empty customer messages.

Prove red/green at actual bridge endpoints with fake socket transport; prove a held
governor attempt later becomes dispatchable, every physical part is paced, and
early/late receipts join across restart and secondary profiles. Communicate any
shared schema additions to root rather than concurrently editing store_schema.py.

## 3. Operator control and CLI

Own `control.py`, `operators.py`, `permissions.py`, CLI messages modules/parsers,
operator command routing/guards, and operator/control tests. Fix R5. Export
`register_business_control_handlers(server, service, *, runner=None, loop=None)`;
root registers it during lifecycle. Marshal async service operations onto the
owning loop from the socket thread. Derive human principals from verified local
peer or configured operator identity, never request-provided capabilities.

Implement real queue/show/edit/approve/reject/tasks/pause/resume/digest/reconcile/
status/readiness/mode and existing lesson/credential/migration operations through
one authenticated operation table. Exact plan digest is required for approval;
CLI errors are nonzero. Both WhatsApp guards route explicit operator commands.
Plugin verbs call `gateway.business.jobs.handle_plugin_request(service, operation,
params, principal)` supplied by task 4. Prove real parser/socket/SQLite behavior
and refuse stale/foreign/ambiguous approvals. No lifecycle/service edits.

## 4. Plugin jobs and actual sales callers

Own fork `plugins_business_client.py`, plugin-host proxy support, new
`gateway/business/jobs.py` and store helpers, plugin integration tests, and the
external `native-training-wheels` worktree. Fix R6, real durable jobs/facts/actions/
outcome cursor and CRM consumers. `handle_plugin_request(service, operation,
params, principal)` is synchronous for control-socket execution; it validates scope
and records durable requests, with async composition marshalled to the owning loop
by task 3 as necessary. Notify task 3 of the precise wire contract.

Migrate actual outreach/reply/followup/setup callers in native mode; no silent
acceptance without a context or offline gateway and no raw send fallback. Keep
explicit legacy mode available until a separate authorized paused cutover. Prove
requests through real PluginContext and isolated plugin-host serialization, real
SQLite persistence, cursor replay/CRM failure, and native command entry points.
Communicate additive schema changes to root. Repair the isolated test wrapper's
local prerequisites and verify the full external suite; compare baseline failures
before attributing unrelated failures.

## Final integration

Run the full business suite, named C7 regression files, bridge Node suite, plugin
wrapper, and `scripts/check`. Replace simulated acceptance tests with a real
inbound → compose → review/edit → exact approval → paced physical dispatch →
receipt → CRM/learning round trip. Review the final diff independently. Update
review/runbook/status documents with evidence, commit/push both repositories as
needed, and update PR #25. Rebase onto main before any future merge; do not merge
or authorize a live pilot in this task.
