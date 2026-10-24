# Native Supervision Workflow and Plugin Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make native supervision usable from the operator's CLI/WhatsApp, preserve learning and protected demo delivery, and prepare a reversible migration off plugin bridge patches.

**Architecture:** The gateway service remains the sole authority for review, scheduling, and evidence. Operator commands, plugin-host requests, and local CLI calls share its authenticated operation table; the external sales plugin becomes a context/policy/job client. Import and cutover tooling operates paused and requires fresh approvals.

**Tech Stack:** Existing argparse/plugin-host/control socket, Python/SQLite, WhatsApp message/reaction events, auxiliary model router, encrypted `agent.vault_store.VaultStore`, existing plugin tests.

**Spec:** [WHATSAPP_TRAINING_WHEELS_DESIGN.md](WHATSAPP_TRAINING_WHEELS_DESIGN.md), §§7–16. Requires [Plan 01](2026-10-24-training-wheels-01-drafting.md), [Plan 02](2026-10-24-training-wheels-02-transport.md), and the [shared contracts](WHATSAPP_TRAINING_WHEELS_PLAN.md).

## Global Constraints

- All external messages remain human supervised; digests cannot bypass pacing and native learning cannot promote autonomy.
- Two rewrites, exact revisions, profile/account scope, unavailable-review audit, and fail-closed required checks remain mandatory on every entry.
- Behavior is YAML/managed configuration; lesson changes are versioned new input, never system-prompt/past-message mutation.
- Secrets remain encrypted in the existing vault and absent from raw queue/event/log/transcript/model payloads. General egress redaction remains enabled.
- Imported approvals reset; source evidence remains attributable; historical `sent` never becomes fabricated `delivered`.
- CRM, research, sales rules and campaign decisions remain in `PLUGIN`, pinned initially to `b914aadd6d6683265008673174fc15e117f0f21f`.
- Tests use temporary homes, the Python project runner, and fake WhatsApp/model boundaries. No live customer send or deployment is authorized by this plan.

## Review Focus

- A stale digest index, ambiguous shorthand, or unauthorized reaction must not approve a different revision — C1.
- A policy/model version change and an inferred later-message association must not improve readiness statistics or activate a lesson — C2.
- A rotated/removed credential or failed dispatch must not leak plaintext or send the old secret — C3.
- A native API outage must not make legacy wrappers fall back to raw HTTP/port 3000 or count held as sent — C4.
- Repeated/interrupted import, missing media, inconsistent old counters, and rollback must leave one paused authority and no live approvals — C5/C6.

---

## File responsibilities

| Create | Responsibility |
| --- | --- |
| `gateway/business/{permissions,operators}.py` | Actor capabilities, cards/digest snapshots, reaction/command controls |
| `hermes_cli/{business_messages,business_messages_client}.py`, `hermes_cli/subcommands/messages.py` | Thin CLI table/parser and authenticated native client |
| `gateway/business/{learning,store_learning}.py` | Review examples, confirmed lessons, revision-aligned metrics |
| `gateway/business/credentials.py` | Explicit credential-release grants and protected rendered plans |
| `gateway/business/{import_legacy,migration}.py` | Read-only inventory/import and paused cutover/rollback state |
| `hermes_cli/plugins_business_client.py` | Scoped serializable plugin-host request proxy |
| `PLUGIN/{native_client,native_jobs}.py` | Sales job submission and cursor-driven CRM consumption |
| `docs/private-fork/TRAINING_WHEELS_OPERATIONS.md` | Operator setup, readiness, migration, pilot and rollback steps |

No dashboard or generic new provider framework is included. Every CLI and plugin
operation below delegates to the existing native service rather than editing SQL.

### Task C1: Authorized operator API, CLI, reactions, and digests

**Files:** Create `gateway/business/permissions.py`, `gateway/business/operators.py`, `hermes_cli/business_messages.py`, `hermes_cli/business_messages_client.py`, `hermes_cli/subcommands/messages.py`, `hermes_cli/plugins_business_client.py`, `tests/gateway/business/test_operator_controls.py`, `tests/hermes_cli/test_business_messages.py`, `tests/plugins/test_business_request_proxy.py`. Modify `hermes_cli/main.py` only for parser wiring, `hermes_cli/plugins.py`, `hermes_cli/plugin_host.py`, `hermes_cli/plugin_host_child.py`, `hermes_cli/plugin_host_wire.py`, `gateway/platforms/base.py::handle_message`, `gateway/run_busy.py`, `gateway/run_adapters.py::_handle_gateway_platform_event`, and native service/control dispatch.

**Interfaces:** `authorize_operation(principal: Principal, scope: Scope, operation: str) -> None`;
async `handle_operator_event(service: BusinessService, event: dict, *, principal: Principal) -> dict`;
`create_digest(principal: Principal, scope: Scope, revision_ids: tuple[str, ...]) -> dict`;
`build_messages_parser(subparsers) -> None`, `messages_command(args) -> int`;
`PluginContext.business_request(operation: str, params: dict) -> dict` with the same
profile-bound serialized proxy in plugin-host mode. This method cannot mint
`Principal`, change profile/account scope, approve, reconcile, grant credentials, or
activate a mode. Its permitted operations are `submit_job`, `submit_candidate`,
`status`, `events`, `ack_events`, `record_fact`, `record_action`.

Native operator operations are `queue`, `show`, `edit`, `approve`, `reject`, `tasks`,
`resolve_task`, `pause`, `resume`, `digest`, `recard`, `reconcile`, `status`,
`readiness`, `set_mode`; later tasks add `lessons`, `review_stats`, `credential_grant`,
`import_legacy`, `cutover`, `rollback`. `events(scope, after: int, *, consumer: str)`
returns ordered `{events, next_cursor}` and persists an acknowledgement only through
`ack_events(consumer, cursor)` after the consumer's successful idempotent write.

- [ ] **Step 1: Write** `test_digest_and_reaction_bind_exact_actor_and_revision`, `test_ambiguous_operator_command_refuses`, `test_plugin_scope_cannot_approve_or_read_foreign_queue`, `test_cli_json_reports_held_and_delivery_distinctly`:

```python
assert stale_digest_result["error"] == "stale_revision"
assert unauthorized_reaction.approval_count == 0
assert ambiguous_command.approval_count == 0
assert plugin_approve_result["error"] == "permission_denied"
assert held_json["state"] == "held"
assert accepted_json["state"] == "accepted"
assert deduplicated_alert_count == 1
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_operator_controls.py tests/hermes_cli/test_business_messages.py tests/plugins/test_business_request_proxy.py -v`; expect absent operator APIs and ctx proxy.
- [x] **Step 3: Implement** table-driven operations and `hermes messages` commands with `--json` and existing profile selection. Local CLI peers use the existing authenticated host-principal policy; plugin-host proxies bind their registered owner/profile. WhatsApp actor identity comes from native provenance and configured operator aliases, never caller tags. Cards show nonsecret preview, checks, reviewer availability, revision and expiry. Store card/digest snapshots and bind number/reaction targets to them; ambiguous shorthand refuses. Notification failures stay durable under B3's bounded lane. Install verified native control dispatch after canonicalization but before the base adapter queues active-session input and before the runner busy guard interrupts an agent. Consumed controls run inline; deduplicate by event id if they reach both guards. All surfaces use the same authorizer. Event cursors are profile/account/consumer-owner scoped and acknowledged only after successful downstream writes.
- [x] **Step 4: Rerun** the task files; exercise an operator reaction while a real gateway turn is blocked, a recreated card, and event replay. Expect one authorized state change and no customer/operator lane confusion.
- [x] **Step 5: Commit** the named control/CLI/plugin-proxy files with `feat(gateway): expose native message review and operator controls`.

### Task C2: Attributable learning and honest reviewer metrics

**Files:** Create `gateway/business/{learning,store_learning}.py`, `tests/gateway/business/test_learning.py`; modify native context/review/approval event writers, shared schema, and C1 command table.

**Interfaces:** Frozen `Lesson(id: str, policy_id: str, version: int, text: str, evidence_revision_ids: tuple[str, ...], state: str)`;
async `propose_lessons(principal: Principal, scope: Scope, *, policy_id: str) -> tuple[Lesson, ...]`;
`confirm_lesson(principal: Principal, scope: Scope, lesson_id: str) -> Lesson`;
`revoke_lesson(principal: Principal, scope: Scope, lesson_id: str, *, reason: str) -> Lesson`;
`confirmed_lessons(scope: Scope, policy_id: str) -> tuple[Lesson, ...]`;
`review_metrics(scope: Scope, *, message_class: str, policy_version: str, model_version: str) -> dict`.
Metrics keys are `reviewed`, `unavailable`, `coverage`, `agreement`, `false_approvals`,
`unpaired`; no automatically actionable `ready_for_auto_send` field is returned.

- [x] **Step 1: Write** `test_only_confirmed_lessons_apply`, `test_metrics_join_same_revision_and_version`, `test_historical_policy_conflict_and_association_are_not_training_truth`:

```python
assert proposed_lesson not in active_lessons
assert confirmed_lesson.text in next_snapshot.confirmed_lessons
assert prior_system_prompt_bytes == original_system_prompt_bytes
assert metrics["unavailable"] == 1
assert metrics["false_approvals"] == 1
assert metrics["unpaired"] == 1
assert inferred_later_send not in eligible_training_pairs
assert account_mode == "supervised"
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_learning.py -v`; expect absent lesson/metrics behavior.
- [x] **Step 3: Implement** `business_lessons`/proposal/evidence records and revision-aligned metric queries. Use `business_lessons` auxiliary routing with 60-second/2,048-token limits and evidence references; no more than five proposed rules per request. An edit/reject without rationale or policy compatibility remains weak/conflicted evidence. Only confirmed active rules enter a newly built context snapshot; preserve prior snapshots/system prompts. Partition reviewer metrics by the revision actually reviewed and the current policy/model version; show unavailable reason/coverage rather than comparing unrelated aggregate counts. Version changes do not inherit readiness. Lesson confirmation/revocation is profile-authorized and invalidates dependent unsent approval plans through their lesson/policy binding.
- [x] **Step 4: Rerun** the task file; include two profiles, stale human review, revoked lesson and a previously approved historical price exception. Expect no cross-profile lesson leakage or policy overwrite.
- [x] **Step 5: Commit** named learning/context/schema/CLI files with `feat(gateway): learn only from confirmed versioned message reviews`.

### Task C3: Explicit protected credential delivery

**Files:** Create `gateway/business/credentials.py`, `tests/gateway/business/test_credentials.py`; extend `agent/vault_store.py` with narrow encrypted-payload methods and tests in `tests/agent/test_vault_business_payloads.py`. Wire render, approvals, dispatch, and C1's `credential_grant` operation; keep `agent/redact.py` default behavior intact.

**Interfaces:** `CredentialGrant(id: str, vault_item_id: str, secret_version: str, scope: Scope, expires_at_ms: int, actor: str)`;
`grant_credential(principal: Principal, scope: Scope, *, vault_item_id: str, expires_at_ms: int) -> CredentialGrant`;
async `prepare_credential_plan(scope: Scope, revision: DraftRevision, *, grants: tuple[CredentialGrant, ...]) -> DeliveryPlan`;
`resolve_approved_plan(scope: Scope, plan: DeliveryPlan) -> tuple[DeliveryPart, ...]`.
Vault methods are `seal_message_payload(binding: dict, payload: dict) -> str` and
`open_message_payload(handle: str) -> dict`; reuse its current encryption/key/lock
facilities in a separate purpose-bound payload namespace. Browser-origin grants
remain unchanged and do not themselves authorize messaging release.

- [x] **Step 1: Write** `test_credential_only_reaches_exact_approved_recipient`, `test_rotation_missing_vault_and_error_paths_never_leak`, `test_approval_binds_final_split_with_secret_materialized`:

```python
assert customer_socket_payload["text"].endswith(secret_sentinel)
assert secret_sentinel not in public_records_logs_or_model_payloads
assert secret_sentinel.encode() not in ordinary_queue_or_database_bytes
assert preview.contains_credential_indicator is True
assert wrong_recipient.socket_calls == []
assert rotated_secret.socket_calls == []
assert computed_materialized_digest == approved_digest  # use B1 digest_delivery_parts
assert rendered_parts == actually_dispatched_parts
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_credentials.py tests/agent/test_vault_business_payloads.py -v`; expect missing purpose-bound encryption/grants.
- [x] **Step 3: Implement** operator-only release from a profile-owned login vault item. Compute an opaque HMAC secret version; bind grant/approval to account/recipient/version/expiry. Materialize/render only inside restricted service scope before approval hashing, encrypt the exact plan, and persist just its handle, HMAC digest, masked preview and metadata in ordinary tables. Protected digests use `hmac-sha256:`; ordinary B1 digests use `sha256:`. Per-part dispatch verification uses the same tagged algorithm and B4 key, never the redacted preview. Resolve after approval and recheck current grant/secret version; rotation requires fresh review. Keep plaintext out of journals/errors, hooks, receipts, traces, transcripts and lessons; sanitize failures without printing payloads. Composer/reviewer see placeholders. Never restore passwords after redaction or disable general egress redaction.
- [x] **Step 4: Rerun** `scripts/run_tests.sh tests/gateway/business/test_credentials.py tests/agent/test_vault_business_payloads.py tests/agent/test_vault_store.py tests/agent/test_redact.py tests/tools/test_browser_vault.py -v`; expect green, including no broader browser-origin permission and no secret in logs after transport rejection.
- [x] **Step 5: Commit** named vault/credential/seam files with `feat(gateway): bind protected credential delivery to exact message approval`.

### Task C4: Native sales job client and durable CRM outcomes

**Files:** In `PLUGIN`, create `native_client.py`, `native_jobs.py`, `tests/test_native_client.py`, `tests/test_native_jobs.py`; extend `tests/run_native.sh` discovery; modify `__init__.py`, `owner_mirror.py`, `jev_triage.py`, `bin/sales-ops-{outreach,followups,replies,queue,learn,product-scan,research,nightly}`, `crm/sqlite_store/gate_reconcile.py`, and `README.md` at their actual callers. Add `tests/plugins/test_sales_business_integration.py` in the fork. Inventory additional direct callers before editing; add their exact paths to this task if found.

**Interfaces:** Plugin `submit_job(*, account: str, contact: str, intent: str, request_id: str, fact_ids: tuple[str, ...], action_ids: tuple[str, ...], message_class: str) -> dict` and
`consume_outcomes(*, consumer_id: str) -> dict` use `ctx.business_request`/C1's
native client, never ports or direct SQL. Native `submit_job` derives trusted scope
and performs A4–A6; `record_fact`/`record_action` accept only the scoped plugin's
business facts/receipts, not another profile's ids or model-asserted action success.

- [x] **Step 1: Write** real plugin/gateway tests `test_outreach_reply_followup_use_native_jobs`, `test_native_outage_has_no_raw_fallback`, `test_crm_consumes_delivery_cursor_idempotently`:

```python
assert queued_result["state"] == "held"
assert queued_result.get("message_id") is None
assert native_api_offline.raw_http_send_count == 0
assert secondary_request.target_account == secondary_account
assert crm_after_replayed_event.conversation_count == 1
assert cursor_after_failed_crm_write == previous_cursor
assert draft_rejection_marks_customer_answered is False
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/plugins/test_sales_business_integration.py -v` and `bash "$sales_plugin_worktree/tests/run_native.sh" --hermes-root "$PWD"`; expect the new native-client/job assertions fail while the baseline remains recorded.
- [x] **Step 3: Implement** native mode selection requiring the service's capabilities; native-mode outage is a visible held/error, never legacy fallback. Keep old live mode until cutover. Replace direct gate operations with native jobs/review/status, classify campaigns/follow-ups in the plugin, and feed business facts/templates to the composer. Avoid double owner mirroring/triage when native provenance already handled it. Change the CRM consumer to native cursor/idempotency records and accurate accepted/delivered/failed/owed states. Remove duplicated script-local model literals: research inherits the profile model unless an explicit plugin config pin is present; judge/lesson work uses native auxiliary routes. Preserve domain rules and research/CRM ownership in the plugin.
- [x] **Step 4: Rerun** both commands; expect all new and existing offline tests green. Assert no native path still writes legacy pending/sent/counter files or restarts the gateway to refresh an allowlist.
- [x] **Step 5: Commit** the named plugin files in its isolated checkout with `feat: use native supervised messaging for sales operations`; commit the fork integration test separately with `test(plugins): exercise native sales messaging through discovery`.

### Task C5: Read-only legacy inventory and idempotent paused import

**Files:** Create `gateway/business/import_legacy.py`, `tests/gateway/business/test_import_legacy.py`, `tests/fixtures/business/legacy/` synthetic fixtures; add C1 CLI operation `import-legacy` and docs. No actual profile backup is read by automated tests.

**Interfaces:** `inventory_legacy(root: Path, *, scope: Scope) -> ImportInventory`;
`ImportInventory(source_digest: str, supported: tuple[dict, ...], attention: tuple[dict, ...], configuration: dict, counters: dict)`;
`import_legacy(store: BusinessStore, inventory: ImportInventory, *, principal: Principal, dry_run: bool = True) -> ImportReport`;
`ImportReport(source_digest: str, imported: int, already_imported: int, task_ids: tuple[str, ...], blockers: tuple[str, ...])`.
The store records unique `(source_digest, legacy_id, scope)` import mappings.

- [x] **Step 1: Write** `test_import_defaults_read_only_and_repeats_safely`, `test_old_sent_is_not_delivered`, `test_missing_media_secret_or_unknown_identity_creates_task`, `test_ambiguous_old_counters_do_not_reset_capacity`:

```python
assert dry_run_database_bytes == before_database_bytes
assert first.imported > 0
assert second.imported == 0
assert imported_live_approval_count == 0
assert imported_legacy_sent.state != "delivered"
assert missing_attachment_task.reason == "legacy_attachment_missing"
assert unresolved_account.dispatch_ready is False
assert imported_account.available_capacity <= provable_available_capacity
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_import_legacy.py -v`; expect absent import contracts.
- [x] **Step 3: Implement** bounded regular-file parsing for the actual pinned gate writers' queue records, scheduled releases, governor state, config, reviewer records, suppression and media. Reject symlinks/traversal/oversized or contradictory records into attention tasks without reading arbitrary paths. Preserve source hashes/provenance and old timestamps; reset every unsent approval and protect credentials via C3 or hold for explicit mapping. Do not infer corrections/delivery/phone identity. Import known limits/timezone exactly; require explicit timezone when absent. Existing counters may lack physical-send evidence: conservatively block remaining capacity until reconciliation/reset boundary instead of inventing a zero count. Persist source/import mappings atomically; interrupted application can resume idempotently.
- [x] **Step 4: Rerun** task tests with real temporary backup trees and injected transaction failures; expect no source writes, no duplicate rows, no lost unsupported records, and zero sends.
- [x] **Step 5: Commit** named importer/fixtures/CLI/docs files with `feat(gateway): inventory and import legacy messaging state while paused`.

### Task C6: Paused ownership transfer and reversible cutover tooling

**Files:** Create `gateway/business/migration.py`, `tests/gateway/business/test_migration.py`; add CLI/control operations, extend `PLUGIN/bin/sales-ops-setup`, `PLUGIN/bin/preflight.sh`, plugin native-client capability checks, and `docs/private-fork/TRAINING_WHEELS_OPERATIONS.md`.

**Interfaces:** `prepare_cutover(principal: Principal, account: AccountKey, *, inventory: ImportInventory, legacy_writers: tuple[dict, ...]) -> CutoverManifest`;
`CutoverManifest(id: str, source_digest: str, account: AccountKey, stage: str, blockers: tuple[str, ...], native_generation: int)`;
async `apply_cutover(principal: Principal, manifest_id: str) -> CutoverManifest`;
async `rollback_cutover(principal: Principal, manifest_id: str) -> CutoverManifest`.
CLI `cutover --plan` is default; `--apply` requires the explicit account/manifest and
an authorized operator. Actual deployment/pilot application remains outside plan execution.

- [x] **Step 1: Write** `test_cutover_requires_quiescent_legacy_owner`, `test_crash_each_cutover_stage_resumes_paused`, `test_rollback_exports_unresolved_work_without_live_approvals`:

```python
assert active_legacy_writer_manifest.blockers
assert incomplete_import_manifest.stage != "ready"
assert crash_reopened_account.paused is True
assert native_and_legacy_active_authorities <= 1
assert rollback_export.live_approval_count == 0
assert rollback_unknown_attempts_are_reconciled is True
assert old_bridge_capability_activation_refused is True
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_migration.py -v`; run the isolated plugin wrapper `bash "$sales_plugin_worktree/tests/run_native.sh" --hermes-root "$PWD"`. Expect absent cutover/rollback contracts.
- [x] **Step 3: Implement** an auditable staged manifest: pause legacy writes/releases, back up, reconcile attempts, import suppression/state, verify stock native bridge/account/capabilities, select native plugin mode, transfer ownership while paused, and permit resume only after readiness. Match identified services/jobs through existing process identity/status helpers and pinned plugin inventory, never argv substring heuristics. No step spawns two schedulers. Native setup skips patch application and verifies capabilities; legacy mode remains explicitly separate until authorized transfer. Rollback pauses native, reconciles attempts, exports unresolved work with approval reset and requires human review before legacy can resume. Unsupported records block affected contacts, not silently vanish.
- [x] **Step 4: Rerun** both test commands with fake service/job controllers and real temporary stores; expect stage idempotency, restrictive rollback, and no live process/config mutation in tests.
- [x] **Step 5: Commit** named fork files with `feat(gateway): prepare reversible paused messaging cutover`; commit plugin setup/preflight changes separately with `feat: validate native messaging instead of patching its bridge`.

### Task C7: Workflow readiness, full offline acceptance, and pilot handoff

**Files:** Create `gateway/business/workflow_readiness.py`, `tests/gateway/business/{test_workflow_readiness,test_full_acceptance}.py`; modify B7 readiness integration and `docs/private-fork/{TRAINING_WHEELS_OPERATIONS,WHATSAPP_TRAINING_WHEELS_DESIGN}.md`. Add `PLUGIN/docs/native-messaging.md` and extend its native integration test.

**Interfaces:** Async `check_workflow_readiness(service: BusinessService, account: AccountKey) -> dict` contributes `workflow_protocol=1` only when operator controls, required policy, credential capability when needed, native plugin mode, import manifest, and exclusive ownership all validate. `pilot_report(scope: Scope) -> dict` reports supervised test evidence without promoting mode or emitting sends. No global requirement that a specific provider/local endpoint must exist is introduced.

- [ ] **Step 1: Write** `test_workflow_blocks_partial_activation`, `test_real_plugin_approval_delivery_and_learning_roundtrip`, `test_native_update_needs_no_patch`:

```python
assert missing_credentials_workflow["ready"] is False
assert incomplete_migration_workflow["ready"] is False
assert fake_end_to_end.states == ("held", "scheduled", "accepted", "delivered")
assert fake_end_to_end.sent_text == exact_approved_text
assert lesson_after_review.state == "proposed"
assert upgraded_stock_bridge.patch_invocations == 0
assert physical_send_times_obey_config is True
```

- [ ] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_workflow_readiness.py tests/gateway/business/test_full_acceptance.py -v`; expect absent complete workflow capability.
- [ ] **Step 3: Implement** readiness integration and the actual-plugin/fake-transport acceptance scenario: inbound/triage, compose/rewrite, operator edit, fresh approval, delayed paced dispatch, early/late receipts, CRM cursor consumption, lesson proposal/confirmation, new-context cancellation, credential release, and restart/rollback. Document setup keys, commands, outcome meanings, paused cutover, and the separate authorized pilot checklist. Keep raw evidence out of commits; summarize sanitized quality/cost/latency measurements and all outstanding unknowns. Full readiness may be tested under fake controllers; it does not authorize a live resume.
- [ ] **Step 4: Run** `scripts/run_tests.sh tests/gateway/business/ tests/plugins/test_business_policy_contract.py tests/plugins/test_business_request_proxy.py tests/plugins/test_sales_business_integration.py tests/hermes_cli/test_business_messages.py tests/agent/test_vault_business_payloads.py -v`, `node --test scripts/whatsapp-bridge/*.test.mjs`, `bash "$sales_plugin_worktree/tests/run_native.sh" --hermes-root "$PWD"`, and `python scripts/check`; require exit 0. Also run `scripts/run_tests.sh tests/gateway/test_gateway_silence_tokens.py tests/gateway/test_normalize_empty_agent_response.py tests/gateway/test_delivery_ledger.py tests/gateway/test_delivery_ledger_process_home.py tests/agent/test_vault_store.py tests/agent/test_redact.py tests/cron/test_delivery_outcome_writeback.py tests/cron/test_cron_multiplex_shared_route_delivery.py tests/cron/test_media_delivery_parity.py tests/gateway/test_whatsapp_multiplex_secret_scope.py tests/gateway/test_whatsapp_media_path_profile.py tests/tools/test_whatsapp_send_message_media.py -v`. Broaden testing only for a demonstrated unresolved concern.
- [ ] **Step 5: Commit** named readiness/acceptance/docs files with `feat(gateway): complete offline supervised messaging readiness`; commit plugin docs/tests with `docs: document native supervised sales messaging cutover`.

## Live pilot and retirement checkpoint (not executed by this plan)

- [ ] Present concrete offline results, dry-run manifest, configured limits, target
  account and operator-owned/test recipients for explicit user authorization.
- [ ] Only after authorization, apply the paused cutover and observe a small supervised
  pilot; verify actual receipt statuses, pacing, operator controls, protected demo
  delivery, service restart, and remaining owed/unknown tasks.
- [ ] After the pilot passes, remove `PLUGIN/bridge/apply_gate_patch.py`,
  `apply_digest_patch.py`, `send_gate.js`, `send_governor.js`, and the legacy queue/
  governor command branches in a follow-up cleanup commit/PR. Preserve portable
  sales-policy rules/interpreter, domain logic, and legacy backups/import support.
  Convert only command aliases with actual callers to native wrappers.
- [ ] Run the plugin offline suite and native full-acceptance suite after removal;
  confirm a stock bridge update and plugin setup invoke no patcher. If the pilot is
  not authorized/passing, keep legacy code dormant and state that retirement remains
  pending; never describe offline readiness as a successful live migration.

## Plan exit

- [ ] All native software and offline verification are reviewable, with no hidden live
  deployment side effects. Remaining pilot/retirement steps have explicit evidence
  and authorization checkpoints rather than guessed completion.
- [ ] Review sanitized semantic evaluation alongside deterministic transport checks.
  Human approval stays enabled regardless of reviewer agreement statistics.
