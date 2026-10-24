# Native Approved WhatsApp Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every supervised WhatsApp send require durable exact approval and account-wide pacing, with honest delivery and crash outcomes.

**Architecture:** Extend Plan 01's native service with a render-bound outbox and transactional account governor. The stock bridge verifies single-use dispatch authorizations, journals physical attempts, and forwards ordinary delivery evidence. Only the native outbox owns recovery for these records.

**Tech Stack:** Existing SQLite/asyncio, Python/Node standard-library HMAC and SHA-256, Express/Baileys `7.0.0-rc13`, real gateway/bridge imports with fake socket tests.

**Spec:** [WHATSAPP_TRAINING_WHEELS_DESIGN.md](WHATSAPP_TRAINING_WHEELS_DESIGN.md), §§5, 7–10, 13–15. Requires completed [Plan 01](2026-10-24-training-wheels-01-drafting.md) and the [shared interfaces](WHATSAPP_TRAINING_WHEELS_PLAN.md).

## Global Constraints

- Supervision starts with every external message approved; two rewrites remain the upstream budget. No automatic promotion or live activation occurs here.
- Bind approval to final content, attachments, recipient, account, reply target, mentions, policy/context revision, and expiry. No post-approval text mutation.
- Every physical send, chunk, edit, reaction, poll, media part, and retry obeys one account governor; unknown classes use conservative outreach policy.
- Dispatch authorizations use `business_dispatch=1`, expire in 30 seconds, and cannot be replayed. Shared records are profile-scoped in launch-home `state.db`.
- Lookup error means held; ambiguous attempt means `unknown` and human reconciliation. `accepted` is not `delivered`.
- Required store/policy/service failure blocks supervised sending; old bridge capabilities never downgrade to raw sending. Personal-mode behavior remains intact.
- Tests use `scripts/run_tests.sh` and `node --test`; no source-reading tests, guessed SDK receipt statuses, customer sends, or new dependencies.

## Review Focus

- Attachment mutation, disappearance, or symlink replacement after approval must refuse — B1/B2.
- New inbound, pause, expiry, or account takeover between claim and socket call must invalidate unused authorization — B2/B4.
- DST gaps/folds, overnight windows, and backward clock movement must preserve windows/caps/spacing — B3.
- Malformed/oversized control messages and stale/forged permit generations must refuse without side effects — B4.
- Early receipts and partial sends across a crash must not produce duplicates or recovered prefixes — B5/B6.

---

## File responsibilities and additional contracts

| Create | Responsibility |
| --- | --- |
| `gateway/business/render.py`, `approvals.py` | Immutable rendered plan and exact-revision authorization |
| `gateway/business/outbox.py`, `store_outbox.py` | Durable scheduling/attempt claims and state transitions |
| `gateway/business/governor.py`, `store_governor.py` | Window/cap eligibility and atomic physical-request reservation |
| `gateway/business/dispatch.py`, `control.py`, `client.py` | Internal transport authorizations, authenticated service verbs/client |
| `gateway/business/transport.py`, `receipts.py`, `recovery.py` | Native bridge executor, delivery-event reconciliation, single recovery owner |
| `scripts/whatsapp-bridge/business_{render,dispatch,receipts,journal}.js` | Pure render, transport guard, receipt normalization, fsynced attempt journal |
| Matching `tests/gateway/business/test_*.py` and `scripts/whatsapp-bridge/business_*.test.mjs` | Behavioral contracts below |

Define `AccountKey(transport_profile: str, account_id: str)` in `models.py` in B2;
it describes the physical sending account independently of the routed runtime.
`render.py` owns frozen `DeliveryPart(index: int, operation: str, payload: dict, media_hash: str | None)`
and `DeliveryPlan(id: str, revision_id: str, scope: Scope, context_version: int, policy_version: str, parts: tuple[DeliveryPart, ...], expires_at_ms: int, digest: str, encrypted_plan_handle: str | None = None)`.
The optional encrypted handle is rejected until C3 installs protected resolution;
do not dispatch unresolved credential slots during this plan.

`Approval(id: str, plan_id: str, revision_id: str, actor: str, approved_at_ms: int, expires_at_ms: int, override_reason: str | None)`
is defined in `approvals.py`. `Attempt(id: str, outbox_id: str, part_index: int, generation: int, state: str, platform_ids: tuple[str, ...], error_code: str | None)`
is defined in `outbox.py`. `Eligibility(allowed: bool, not_before_ms: int | None, reason: str | None)`
is defined in `governor.py`. `DeliveryEvent(account: AccountKey, message_id: str, contact_id: str, status: str, timestamp_ms: int, code: str | None)`
is defined in `receipts.py`. These dataclasses expose validated JSON wire forms.

### Task B1: Render the exact approval plan before authorization

**Files:** Create `gateway/business/render.py`, `scripts/whatsapp-bridge/business_render.js`, `tests/gateway/business/test_render.py`, `scripts/whatsapp-bridge/business_render.test.mjs`. Modify `scripts/whatsapp-bridge/bridge.js` only to wire a non-sending `/business/render` route and reuse its existing format/chunk/payload helpers. Extend `store_drafts.py` for rendered plans and captured attachment references.

**Interfaces:** Async `render_delivery_plan(scope: Scope, revision: DraftRevision, *, now_ms: int) -> DeliveryPlan`;
`digest_delivery_parts(parts: tuple[DeliveryPart, ...], *, binding: dict, hmac_key: bytes | None = None) -> str`;
Node `renderBusinessPlan(request, {messageStore, readAttachment}) -> object`.
The binding has the plan scope, revision/context/policy/lesson versions, expiry,
reply target, and mentions. Both Python and Node digest fixtures use this exact field set.
Use existing outgoing formatting/chunking and quote/mention payload helpers. Canonical
hash input is sorted-key UTF-8 JSON with no NaN/Infinity, plus hashes of captured
attachment bytes; volatile SDK encryption ids are excluded, semantic payload is not.
Tag ordinary digests `sha256:`; C3 adds `hmac-sha256:` for protected plans.
Only `text | media | edit | reaction | poll | location` operations are accepted.

- [x] **Step 1: Write** `test_approval_hash_covers_every_delivery_field` and `test_render_captures_and_rechecks_attachment_bytes`; Node tests call the real formatter/chunker with fake message storage:

```python
assert plan.parts[0].payload["text"] == expected_whatsapp_rendering
assert changed_reply_target.digest != plan.digest
assert changed_mentions.digest != plan.digest
assert changed_attachment.digest != plan.digest
assert render_socket_send_count == 0
with pytest.raises(BusinessProtocolError):
    render_changed_or_symlinked_capture()
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_render.py -v` and `node --test scripts/whatsapp-bridge/business_render.test.mjs`; expect absent rendering/capture contracts.
- [x] **Step 3: Implement** native capture using existing attachment capture/regular-file/hash primitives; bind handles to profile/account. Copy bytes into service-owned storage before approval, reject missing/foreign/symlinked media, and verify hash at use. Render exactly once before approval; store the immutable semantic plan. An expired reply target or unavailable quote fails rather than silently changing to an unquoted message. Reject credential-bearing plans until C3. No actual send exists on the render route.
- [x] **Step 4: Rerun** both commands; expect green for Unicode/chunk boundaries, captions, missing quote targets, changed media, and each operation kind.
- [x] **Step 5: Commit** named render/seam/test files with `feat(whatsapp): render immutable message plans for exact approval`.

### Task B2: Exact approval and durable, stale-safe outbox

**Files:** Create `gateway/business/{approvals,outbox,store_outbox}.py`, `tests/gateway/business/test_approvals_outbox.py`; extend `store_schema.py`, `service.py`, and `conftest.py` with `business_account`.

**Interfaces:** Async `submit_revision(scope: Scope, revision_id: str, *, request_id: str) -> Receipt`;
`approve_revision(principal: Principal, scope: Scope, *, revision_id: str, plan_digest: str, override_reason: str | None = None) -> Approval`;
`reject_revision(principal: Principal, scope: Scope, *, revision_id: str, reason: str) -> Receipt`;
`claim_part(account: AccountKey, *, now_ms: int) -> Attempt | None`;
`invalidate_contact(scope: Scope, *, reason: str) -> int`.
Approval is idempotent for the same actor/revision/digest. A new approval after expiry
has a new audit identity. Claimed dispatch rechecks the current context/policy/ownership.

- [x] **Step 1: Write** `test_held_receipt_is_not_send_success`, `test_approval_is_exact_and_idempotent`, and `test_late_results_and_suppression_cannot_resurrect_approval`:

```python
assert submitted.state == "held"
assert submitted.revision_id == revision.id
assert not hasattr(submitted, "message_id")
assert second_approval.id == first_approval.id
assert after_new_inbound_dispatches == []
assert after_policy_change_dispatches == []
assert after_suppression_dispatches == []
with pytest.raises(BusinessConflict):
    approve_old_digest()
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_approvals_outbox.py -v`; expect absent outbox/approval behavior.
- [x] **Step 3: Implement** versioned `business_plans`, `business_approvals`, `business_outbox`, `business_attempts` tables with unique submission key and one active account claim. Required checks must pass; an advisory reject/unavailable review needs explicit nonempty human override reason. Store failure blocks submission/approval. Default approval/queue expiry comes from A1; expiry creates a visible attention event, never an answered episode. A changed revision, account, context, policy, lesson version, attachment, or suppression invalidates approval and unused authorizations. Rejected/expired/superseded drafts do not settle an owed episode.
- [x] **Step 4: Rerun** the task file; use concurrent real SQLite transactions to prove only one claim succeeds. Verify an invalidated late review/compose result remains superseded after reopen.
- [x] **Step 5: Commit** the task files with `feat(gateway): authorize exact draft revisions in a durable outbox`.

### Task B3: Transactional account pacing and sending windows

**Files:** Create `gateway/business/{governor,store_governor}.py`, `tests/gateway/business/test_governor.py`; extend config/schema/outbox for governor policy/counters.

**Interfaces:** `eligibility(account: AccountKey, *, message_class: str, now_ms: int) -> Eligibility`;
`reserve_attempt(account: AccountKey, attempt_id: str, *, message_class: str, now_ms: int) -> Eligibility`;
`complete_attempt(account: AccountKey, attempt_id: str, *, submitted: bool, finished_at_ms: int) -> None`;
`pause_account(principal: Principal, account: AccountKey, *, reason: str) -> None`;
`resume_account(principal: Principal, account: AccountKey) -> None`.
Required governor fields: `timezone`, `windows` by `cold/followup/reply`, `min_gap_seconds`,
`reply_min_gap_seconds`, `jitter_seconds`, `caps`, `hard_max_per_day`,
`failure_window_seconds`, `failure_count`. Preserve configured values; don't invent outreach defaults.

`windows[class]` is a list of `{weekdays: list[int], start: "HH:MM", end: "HH:MM"}`
with Monday `0`, Sunday `6`; `24:00` is permitted only as an end. Equal start/end
is invalid; a lower end than start means an overnight interval. `caps[class]` has
`per_hour` and `per_day`, each positive integer or null; `hard_max_per_day` is a
required positive integer. All three classes require explicit windows/caps. Gaps,
jitter and thresholds are numeric nonnegative/positive as appropriate; missing
required values hold. C5 translates the legacy field spellings without changing values.

- [x] **Step 1: Write** `test_batch_and_chunks_share_physical_capacity`, `test_restart_and_reconfiguration_do_not_burst`, `test_dst_overnight_and_clock_regression`:

```python
assert concurrent_reservations_allowed == 1
assert attempts_today == physical_requests_attempted
assert min_actual_gap_ms >= configured_gap_ms
assert closed_window.allowed is False
assert missing_config.reason == "governor_unconfigured"
assert backward_clock.allowed is False
assert dst_fold_counts == expected_single_local_day_counts
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_governor.py -v`; expect missing policy/reservation behavior.
- [x] **Step 3: Implement** `zoneinfo` window calculation with overnight windows explicitly split across days; DST gaps advance to the next valid instant and folds cannot reset counters twice. Use timestamped physical attempts for rolling hourly buckets and timezone-local daily buckets. Sample jitter once per persisted eligibility slot; don't resample every poll. One in-flight request per account plus a monotonic floor prevents parallel bursts. A backward UTC jump holds until a conservative elapsed-time/account watermark permits dispatch. Pause bumps the account generation; resumed/config-changed work is reevaluated. Refund only a proved unsubmitted reservation. Treat invalid windows/negative caps as configuration errors.
- [x] **Step 4: Rerun** the task file with clock-controlled concurrent approval/restart cases and no sleeps; expect all configured caps/gaps respected, including replies and operator-lane resource limits.
- [x] **Step 5: Commit** the task files with `feat(whatsapp): pace every physical send through an account governor`.

### Task B4: Native control protocol and the bridge dispatch backstop

**Files:** Create `gateway/business/{control,client,dispatch}.py`, `scripts/whatsapp-bridge/{business_dispatch,business_journal}.js`, `tests/gateway/business/test_dispatch_protocol.py`, `scripts/whatsapp-bridge/business_dispatch.test.mjs`. Wire existing private handlers in `gateway/run_bootstrap.py`; modify bridge sender/HTTP endpoints and scoped spawn configuration in `plugins/platforms/whatsapp/adapter.py`.

**Interfaces:** `handle_business_request(service: BusinessService, *, operation: str, params: dict, principal: Principal) -> dict` is the common internal API. Local control verbs are `business.request` and `business.transport`; reuse `GatewayControlServer.private_handlers` and its socket/pipe ACL/protocol limits. `request_business(home: Path, *, operation: str, params: dict) -> dict` raises typed offline/protocol errors and never falls back to raw send.
`issue_authorization(account: AccountKey, attempt: Attempt, plan: DeliveryPlan, *, now_ms: int) -> str`;
`consume_authorization(token: str, *, payload_digest: str, now_ms: int) -> Attempt`.
Node async `dispatchAuthorized(request, {authorize, journal, sendSocket, clock}) -> object`.

- [ ] **Step 1: Write** native-control and Node tests: `test_missing_forged_foreign_expired_or_consumed_permit_refuses`, `test_pause_or_takeover_fence_refuses`, `test_oversized_and_malformed_control_request_is_inert`:

```python
assert missing_permit.error == "business_authorization_required"
assert replay.error == "authorization_consumed"
assert paused_generation.socket_calls == []
assert foreign_scope.socket_calls == []
assert invalid_request.business_row_count == before_count
assert unverified_result["error"] == "permission_denied"
```

- [ ] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_dispatch_protocol.py -v` and `node --test scripts/whatsapp-bridge/business_dispatch.test.mjs`; expect missing backstop behavior.
- [ ] **Step 3: Implement** sorted canonical JSON/HMAC-SHA256 authorizations binding `business_dispatch=1`, account/profile/part/digest/attempt/generation and 30-second expiry. Generate separate 256-bit transport credentials in a service-owned 0600 file, never model/config output; scope plugin clients to submission operations only. Control principals derive from verified local/host provenance; caller fields cannot assert role/profile. The bridge verifies the token, fsyncs a consumed-attempt entry, and asks the native service to atomically recheck/consume against current state before socket submission. All endpoints and the final socket helper require it in latched mode, including media/edit/location/poll/reaction. API work that needs models is queued and returns a receipt, not a blocking control call. Native side-effect-free rendering remains available with authenticated access.
- [ ] **Step 4: Rerun** both commands; ensure a service outage, journal write failure, or unverifiable expiry produces zero socket calls, and legacy raw callers get a typed refusal. Exercise Windows pipe behavior on Windows, never by faking `sys.platform`.
- [ ] **Step 5: Commit** only named protocol/bridge/seam files with `feat(whatsapp): require native one-use authorization for supervised dispatch`.

### Task B5: Physical execution, registration checks, and typed send receipts

**Files:** Create `gateway/business/transport.py`, `tests/gateway/business/test_transport.py`. Modify `gateway/business/turns.py`, `gateway/business/context.py`, `gateway/business/store_episodes.py` for staged working intents; `plugins/platforms/whatsapp/adapter.py` send/edit/media/poll/reaction/standalone seams; `gateway/platforms/base.py::SendResult` and its retry/media callers; `tools/send_message_tool.py`, `cron/scheduler_delivery.py`, and `gateway/session_ingress.py::deliver_response` for explicit queued outcomes.

**Interfaces:** Async `dispatch_due(service: BusinessService, account: AccountKey) -> tuple[Receipt, ...]`;
async `preflight_recipient(scope: Scope) -> str`, returning `known_valid | no_whatsapp_account | unknown`;
`submit_external(scope: Scope, candidate: Candidate, *, request_id: str, source_kind: str) -> Receipt` is async and enters native checks/composition as appropriate, never raw transport.
For a tool send to the active turn's own contact, use
`stage_turn_candidate(scope: Scope, episode: EpisodeRef, candidate: Candidate) -> Receipt`:
it returns `state="staged"` and contributes to that episode's single terminal draft,
not a second immediately reviewable message. The episode comes from verified turn
context. A send to a different contact is a separate job. The terminal handler
includes staged text/media intents in composer context and commits at most one
active revision/ordered delivery plan for that episode.
Add optional `business_receipt: dict | None = None` to `SendResult`. A held result has
`success=False`, `retryable=False`, `message_id=None`, and a typed business receipt.
All owned callers branch on that receipt before generic error/retry logic. Nonbusiness results are unchanged.

- [ ] **Step 1: Write** a parameterized real-path test for final, tool, cron, standalone, media, edit, reaction, poll and location; add `test_registration_error_holds`, `test_chunk_timeout_is_unknown`:

```python
assert queued.business_receipt["state"] == "held"
assert queued.success is False
assert queued.message_id is None
assert generic_retry_count == 0
assert own_turn_tool_receipt["state"] == "staged"
assert active_drafts_after_tool_and_final == 1
assert negative_registration.state == "failed"
assert negative_registration.reason == "no_whatsapp_account"
assert lookup_error.state == "held"
assert timeout_after_socket.state == "unknown"
assert subsequent_parts_dispatched == []
```

- [ ] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_transport.py -v`; expect queued/outbox/send paths to fail the new assertions.
- [ ] **Step 3: Implement** claimed per-part dispatch through B3/B4. Known valid recent inbound satisfies preflight; otherwise use `onWhatsApp` for a resolvable phone target. Empty negative fails, lookup error holds, unresolved LID never becomes a guessed number. Resolve existing bridge ownership and require reported session/account proof. Execute the approved payload with no formatting fallback. Stop on partial/unknown outcomes and preserve earlier part ids. Route alternate callers into submission; own-contact working-tool sends stage intent until the terminal draft rather than compete with final response delivery. Cron submission keys use execution identity so retries are idempotent without suppressing future runs. Ensure text/media base retry loops recognize held/staged as queued, not success or retryable failure.
- [ ] **Step 4: Rerun** the task file plus `scripts/run_tests.sh tests/gateway/test_whatsapp_native_delivery.py tests/tools/test_whatsapp_send_message_media.py -v`; expect green, including no port-3000/default-bot fallback for a secondary.
- [ ] **Step 5: Commit** the named transport/caller/test files with `feat(gateway): route all WhatsApp egress through supervised submission`.

### Task B6: Delivery evidence and a single crash-recovery owner

**Files:** Create `gateway/business/{receipts,recovery}.py`, `scripts/whatsapp-bridge/business_receipts.js`, `tests/gateway/business/test_delivery_recovery.py`, `scripts/whatsapp-bridge/business_receipts.test.mjs`. Wire `bridge.js` `messages.update`, the adapter platform-event path, and `gateway/delivery_ledger.py`'s sweeps/registration seam.

**Interfaces:** `apply_delivery_event(store: BusinessStore, event: DeliveryEvent) -> Receipt | None`;
async `reconcile_account(service: BusinessService, account: AccountKey, *, journal: tuple[dict, ...]) -> tuple[Receipt, ...]`;
`owns_business_delivery(scope: Scope, source_id: str) -> bool` is used by legacy ledger callers.
Node `normalizeDeliveryUpdates(updates, {account, statusEnum}) -> object[]`; import
the exact receipt status enum from the installed/pinned Baileys package during implementation.

- [ ] **Step 1: Write** `test_ack_before_send_result_and_duplicate_acks`, `test_partial_delivery_aggregate`, `test_crash_at_each_dispatch_boundary`, `test_legacy_sweep_cannot_resend_business_item`:

```python
assert accepted.state == "accepted"
assert accepted_episode.state == "awaiting_delivery"
assert after_one_of_two_parts.state != "delivered"
assert after_all_required_parts.state == "delivered"
assert late_ack_before_http_response_is_joined is True
assert unknown_attempt.automatic_retry_count == 0
assert customer_recovered_prefixes == []
assert legacy_sweep_business_socket_calls == []
```

- [ ] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_delivery_recovery.py -v` and `node --test scripts/whatsapp-bridge/business_receipts.test.mjs`; expect missing status/recovery contracts.
- [ ] **Step 3: Implement** idempotent receipt storage with account/message-id joins, retaining early orphan receipts and not regressing delivered on stale updates. Forward ordinary acks without breaking existing poll updates. Reconcile fsynced bridge attempt entries with outbox attempts: consumed/in-flight without terminal evidence is unknown; proved unsubmitted may retry under B3. Late authoritative receipts resolve unknowns; absent receipt never proves failure. Preserve per-part outcomes and create deduplicated permanent/abandoned/unknown human tasks. Business ids bypass legacy best-effort recovery and its visible prefixes; do not rewrite legacy states or behavior globally.
- [ ] **Step 4: Rerun** both commands and the existing delivery-ledger tests selected by `scripts/run_tests.sh tests/gateway/test_delivery_ledger.py tests/gateway/test_delivery_ledger_process_home.py tests/gateway/test_delivery_ledger_producer.py -v`; expect green. Inject process death before/after journal fsync, consume commit, socket call, socket response, and outbox completion using events/subprocesses, not timing sleeps.
- [ ] **Step 5: Commit** the named receipt/recovery files with `feat(whatsapp): reconcile delivery evidence without blind business retries`.

### Task B7: Persistent supervision, readiness, and complete path verification

**Files:** Create `gateway/business/readiness.py`, `tests/gateway/business/{test_readiness,test_egress_matrix}.py`. Modify service lifecycle, adapter connect/health/status, native turn result/voice/footer/progress seams from A7, `gateway/run_adapters.py` runtime status, and `scripts/whatsapp-bridge/bridge.js` auth close/health handling. Extend `docs/private-fork/TRAINING_WHEELS_OPERATIONS.md`.

**Interfaces:** Async `check_readiness(service: BusinessService, account: AccountKey) -> dict` with `ready: bool`, `blocking: list[str]`, `warnings: list[str]`;
async `set_business_mode(principal: Principal, account: AccountKey, *, mode: str) -> dict`;
`record_bridge_state(account: AccountKey, *, state: str, code: str | None) -> None`.
Readiness checks policy, operator identity, complete governor, writable/journaled store,
verified bridge protocol/account, scoped model probes, and recovery ownership. No live
supervised activation is allowed until C7's workflow readiness checks also pass.

- [ ] **Step 1: Write** `test_latch_survives_missing_config_and_old_bridge`, `test_auth_revocation_stops_respawn`, `test_all_egress_paths_obey_native_authority`:

```python
assert absent_policy_readiness["ready"] is False
assert downgraded_bridge.socket_calls == []
assert revoked.state == "needs_reauth"
assert revoked.respawn_count == 0
assert failed_turn.customer_socket_calls == []
assert configured_business_raw_footer_voice_progress_calls == []
assert account_mode_after_failed_activation != "supervised"
```

- [ ] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_readiness.py tests/gateway/business/test_egress_matrix.py -v`; expect missing latch/readiness/egress protections.
- [ ] **Step 3: Implement** the persistent account latch and capability stamp, service-required health, and terminal `needs_reauth` propagation on real logout/device-removal events rather than generic status substring matching. Complete supervised turn separation: no ordinary final normalization, footer, TTS, progress, clarify/tool-approval prompt, or recovery message can reach an external contact through an unapproved path. Operator controls/clarifications use verified operator destinations. Require workflow readiness capability from C7 before activation; until then report the missing capability explicitly. Continue shadow/personal behavior as A7 specifies.
- [ ] **Step 4: Run** the task files, `scripts/run_tests.sh tests/gateway/business/ tests/gateway/test_whatsapp_connect.py tests/gateway/test_whatsapp_multiplex.py tests/gateway/test_gateway_silence_tokens.py -v`, `node --test scripts/whatsapp-bridge/*.test.mjs`, and `python scripts/check`; require exit 0. Re-run only for changes/failures, not repeatedly after green.
- [ ] **Step 5: Commit** named readiness/matrix/seam files with `feat(whatsapp): fail closed on incomplete supervised account readiness`.

## Plan exit

- [ ] Verify the acceptance matrix covers raw HTTP plus every actual gateway/tool/cron/standalone entry, including multi-profile restart and missing dispatch credentials in sandboxed scope.
- [ ] Record offline evidence and remaining workflow capability blockers in the PR. No account is silently upgraded or enabled.
- [ ] Proceed to Plan 03; the plugin's live patch remains intact until the explicitly authorized cutover.
