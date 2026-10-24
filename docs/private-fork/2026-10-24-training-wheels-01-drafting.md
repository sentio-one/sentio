# Supervised Business Drafting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce durable, policy-checked customer drafts and explicit non-message outcomes in shadow mode without giving working-agent prose a customer audience.

**Architecture:** Add focused `gateway/business/` modules backed by the gateway-owned SQLite store. Native ingress preserves provenance; required plugin contributions feed a tool-free auxiliary composer and bounded reviewer/revision loop. Existing live supervision stays in place during this plan.

**Tech Stack:** Existing Python dataclasses/SQLite/asyncio, Hermes plugin registry/host, auxiliary model routing, pytest via the project runner.

**Spec:** [WHATSAPP_TRAINING_WHEELS_DESIGN.md](WHATSAPP_TRAINING_WHEELS_DESIGN.md), especially §§5–8, 11–13. Read the [shared plan and interface definitions](WHATSAPP_TRAINING_WHEELS_PLAN.md) first.

## Global Constraints

- All shared-plan constraints apply. Two rewrites; 60-second/2,048-token auxiliary calls; 10-second required-policy deadline.
- Shared `business_*` tables are in the launch home's `state.db`; callbacks and model calls bind the owning runtime profile.
- `no_reply`, `escalate`, and `failed` produce zero customer copy. Schemas do not replace semantic checking or human review.
- The working agent retains a stable prompt prefix; task instructions/lessons append as new input. Do not add a core model tool.
- This plan sends nothing natively. Mode defaults to `off`; shadow comparison cannot change legacy live behavior.
- Python tests use `scripts/run_tests.sh`, temporary homes, and real imports. Plugin changes occur only in its isolated checkout.

## Review Focus

- Scalar/oversized/malformed outcome JSON must become a protocol failure — A1/A5.
- Duplicate inbound delivery must not increment context twice or duplicate human tasks — A2/A4.
- Alias/profile changes after restart must preserve ownership and suppression — A2/A4.
- Available voice transcripts and owner replies must not be misread as missing input/customer prompts — A4.
- Late compose/review results must not overwrite newer context or a human revision — A5/A6.

---

## File responsibilities

| Create | Responsibility |
| --- | --- |
| `gateway/business/__init__.py`, `models.py`, `config.py` | Package, immutable validated records, raw-YAML/managed-overlay config |
| `gateway/business/store.py`, `store_schema.py`, `store_episodes.py`, `store_drafts.py` | Connection/transactions, additive schema, episodes/suppression/events, immutable revisions/tasks |
| `gateway/business/policy.py`, `context.py`, `ingress.py` | Required plugin contributions, context snapshots, admission/owner/reaction provenance |
| `gateway/business/decision.py`, `compose.py`, `review.py` | Structured working outcome, dedicated composition, bounded checks/review/revisions |
| `gateway/business/service.py`, `lifecycle.py`, `turns.py` | Component wiring, startup/shutdown/scoped workers, turn integration |
| `hermes_cli/plugins_business.py` | Required business-hook ownership and bounded typed dispatch through the existing manager |
| `tests/gateway/business/conftest.py`, named test files below | Temporary real components plus fake model/transport boundaries |
| `PLUGIN/native_policy.py`, `PLUGIN/content_policy.py` | Concrete sales consumer and pure profile-configured checks |

Modify only the existing seams named per task; keep facade edits to declarations/wiring.

### Task A1: Validated mode and business outcome records

**Files:** Create `gateway/business/{__init__,models,config}.py`, `tests/gateway/business/{conftest,test_config}.py`. Modify `hermes_cli/config_defaults.py` (auxiliary defaults) and `website/docs/developer-guide/gateway-internals.md` (mode contract).

**Interfaces:** Produces the frozen types/fields listed in the shared plan;
`parse_decision(payload: object) -> WorkDecision`,
`parse_candidate(payload: object) -> Candidate`,
`load_business_config(raw: dict, *, supervised_latch: bool) -> BusinessConfig`.
`BusinessConfig` holds exactly the shared-plan YAML fields; `enabled` is derived
from mode, never a separate flag. Invalid config raises `BusinessConfigError(code: str)`;
invalid envelopes raise `BusinessProtocolError(code: str)`. No new type is implicitly truthy permission.

- [x] **Step 1: Write the failing tests.** Add `test_invalid_config_cannot_disable_a_latched_account`, `test_rewrite_and_aux_defaults`, and `test_invalid_outcome_is_not_customer_text` with these assertions:

```python
assert load_business_config({}, supervised_latch=False).mode == "off"
assert config.rewrite_attempts == 2
assert config.approval_ttl_seconds == 86400
assert config.queue_ttl_seconds == 604800
with pytest.raises(BusinessConfigError):
    load_business_config({"mode": "off"}, supervised_latch=True)
for payload in ("no reply sent", [], {"kind": "send"}, {"kind": "compose"}):
    with pytest.raises(BusinessProtocolError):
        parse_decision(payload)
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_config.py -v`; expect missing-module/API failure before implementation.
- [x] **Step 3: Implement** those types/parsers/config reader, rejecting unknown envelope fields, missing references, invalid windows/config scalars, and JSON above 64 KiB. Add the three `_aux(60)` defaults; inherit provider/model through existing routing. Configuration never reads ambient profile secrets.
- [x] **Step 4: Run** the same command; expect all assertions green. Verify existing empty/personal config still loads without a business service.
- [x] **Step 5: Commit** the task's exact files: `git add gateway/business tests/gateway/business/conftest.py tests/gateway/business/test_config.py hermes_cli/config_defaults.py website/docs/developer-guide/gateway-internals.md`, then `git commit -m "feat(gateway): define supervised business messaging contracts"`.

### Task A2: Profile-scoped durable episodes, revisions, and tasks

**Files:** Create `gateway/business/{store,store_schema,store_episodes,store_drafts}.py`, `tests/gateway/business/test_store.py`. Extend `models.py` for persisted record fields and the shared `BusinessConflict`/`BusinessDenied` exceptions used here.

**Interfaces:** Consumes A1. Produces `BusinessStore(launch_home: Path)` and methods:
`record_ingress(scope: Scope, event: ContextEvent, *, source_id: str) -> EpisodeRef`;
`record_verdict(scope: Scope, episode_id: str, *, kind: str, reason: str) -> Receipt`;
`save_snapshot(snapshot: ContextSnapshot) -> None`;
`get_snapshot(scope: Scope, snapshot_id: str) -> ContextSnapshot`;
`append_revision(scope: Scope, episode: EpisodeRef, candidate: Candidate, *, expected_revision_id: str | None, policy_version: str, lesson_version: int) -> DraftRevision`;
`get_revision(scope: Scope, revision_id: str) -> DraftRevision`;
`list_events(scope: Scope) -> tuple[ContextEvent, ...]`;
`set_suppressed(scope: Scope, *, reason: str, source_id: str) -> None`;
`is_suppressed(scope: Scope) -> bool`;
`put_task(scope: Scope, *, dedupe_key: str, reason: str, refs: tuple[str, ...]) -> str`.
Conflict raises `BusinessConflict(code: str)`; foreign rows raise `BusinessDenied`.

- [x] **Step 1: Write** `test_ingress_idempotency_and_profile_isolation`, `test_revision_compare_and_swap`, and `test_suppression_survives_alias_binding_and_restart`:

```python
first = store.record_ingress(scope, event, source_id="inbound-1")
assert store.record_ingress(scope, event, source_id="inbound-1") == first
revision = store.append_revision(scope, first, candidate, expected_revision_id=None,
                                 policy_version="p1", lesson_version=0)
with pytest.raises(BusinessDenied):
    store.get_revision(foreign_scope, revision.id)
with pytest.raises(BusinessConflict):
    store.append_revision(scope, first, candidate, expected_revision_id="stale",
                          policy_version="p1", lesson_version=0)
assert reopened.is_suppressed(scope) is True
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_store.py -v`; expect absent store behavior.
- [x] **Step 3: Implement** WAL transactions and versioned tables `business_accounts`, `business_contacts`, `business_ingress`, `business_episodes`, `business_contexts`, `business_revisions`, `business_tasks`, `business_events`. Unique source/profile/account and revision-parent keys enforce idempotency/CAS. Reject a changing payload under the same source id. Store no plaintext credential fields. Retain immutable records; expose only owned rows. Native contact records have stable UUIDs; attach aliases using the existing resolver. An alias collision between two native records holds both for reconciliation, rather than silently merging approvals or suppressions. Account ownership is explicit, never guessed from a phone/port.
- [x] **Step 4: Run** the same command; expect green, including a forced commit failure yielding no revision/task receipt. Reopen a real temporary SQLite file rather than mocking restart.
- [x] **Step 5: Commit** `gateway/business/` and `tests/gateway/business/test_store.py` with message `feat(gateway): persist business episodes and immutable draft revisions`.

### Task A3: Required plugin policy with a concrete sales consumer

**Files:** Create `gateway/business/policy.py`, `hermes_cli/plugins_business.py`, `tests/plugins/test_business_policy_contract.py`; modify hook declarations in `hermes_cli/plugins.py` and isolated-host dispatch in `hermes_cli/plugin_host_wire.py` only if serializable hook support requires it. In `PLUGIN`, create `native_policy.py`, `content_policy.py`, `tests/test_native_policy.py`, `tests/run_native.sh`; modify `__init__.py`, `tests/run_all.sh`, and `bin/sales-ops-outreach` to reuse the pure checks and run native tests.

**Interfaces:** `collect_policy(scope: Scope, episode: EpisodeRef) -> PolicyBundle` is async;
`check_candidate(scope: Scope, candidate: Candidate, *, bundle: PolicyBundle) -> CheckReport` is async.
`PolicyBundle(id: str, version: str, facts: tuple[Fact, ...], actions: tuple[ActionReceipt, ...], rules: tuple[str, ...], criteria: tuple[str, ...], missing: tuple[str, ...])`.
Registered hooks are exactly `business_context` and `business_check`; hook results are JSON objects, tagged with callback owner by the native registry. Required ids come from `required_policies`, not the result's claimed owner.
Plugin callbacks: `business_context(*, episode: dict, scope: dict) -> dict` and
`business_check(*, candidate: dict, scope: dict, policy_version: str) -> dict`.

- [x] **Step 1: Write** real-discovery tests for in-process and isolated plugins: `test_missing_timeout_or_invalid_required_policy_blocks`, `test_callback_owner_cannot_be_spoofed`, and `test_native_sales_checks_match_existing_cases`:

```python
assert missing_report.ok is False
assert timed_out_report.issues[0]["code"] == "policy_unavailable"
assert forged_owner_report.ok is False
assert native_report.issues == legacy_expected_issues
assert loaded_plugin_has_callback("business_context")
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/plugins/test_business_policy_contract.py -v` and `python3 -m unittest discover -s "$sales_plugin_worktree/tests" -p test_native_policy.py`; expect absent native behavior. The new native test must seed a temporary profile before importing configuration. Do not invoke the old all-suite runner against its default live-profile fallback.
- [x] **Step 3: Implement** bounded required dispatch over the existing callback ownership ledger, scoped manager, and host transport, with 10-second deadline and fail-closed missing/error results. Preserve old callback signature filtering. Extract the plugin's pure rule interpreter, passing configuration per call; preserve content-lint parity and its existing limits rather than moving sales rules to core. Add dormant native callbacks to the real plugin registration; legacy sending remains enabled until C4/C7 cutover. Implement `run_native.sh --hermes-root PATH` as specified in the shared plan, with isolated home/config, native-test discovery and explicit temporary sources for every legacy suite.
- [x] **Step 4: Rerun** the fork command and `bash "$sales_plugin_worktree/tests/run_native.sh" --hermes-root "$PWD"`; expect the new discovery/host/parity tests and existing plugin suite green. A missing plugin must never become an empty successful report.
- [x] **Step 5: Commit** the fork task files with `feat(plugins): support required business policy contributions`; commit only the named plugin files in its isolated checkout with `feat: expose native sales policy without bridge mutation`.

### Task A4: Admission ledger and complete customer context

**Files:** Create `gateway/business/{ingress,context}.py`, `tests/gateway/business/test_ingress_context.py`. Modify `gateway/run_inbound.py::_hm_admit_event`, `gateway/run_inbound_hooks.py::_hm_pre_gateway_dispatch_hook`, `gateway/platforms/webhook_ingress.py::admit_producer`, and `plugins/platforms/whatsapp/adapter.py::_build_message_event` only where typed provenance is absent.

**Interfaces:** Async `observe_ingress(runner, event: MessageEvent) -> EpisodeRef | None` and
`finish_ingress(runner, event: MessageEvent, *, verdict: str, reason: str) -> None`;
`build_context(store: BusinessStore, scope: Scope, episode: EpisodeRef, *, policy: PolicyBundle, lesson_version: int) -> ContextSnapshot`.
Capture canonical `RoutingIdentity` before keys; use receiving adapter/account rather than the default bot. Native records exist before pre-dispatch hooks can skip. Record later access refusal as refused, never an authorized turn. Owner/control events are recorded once but not run as customer prompts.

- [x] **Step 1: Write** `test_consumed_message_remains_in_worklist`, `test_duplicate_ingress_and_owner_reply`, `test_voice_and_reaction_context`, `test_restored_secondary_never_uses_launch_account`:

```python
assert skipped_row.verdict == "escalate"
assert task.refs == (skipped_row.episode_id,)
assert duplicate.context_version == original.context_version
assert snapshot.events[-1].actor == "owner"
assert "recognized voice transcript" in [e.text for e in voice_snapshot.events]
assert reaction_snapshot.events[-1].target_id == offer_id
assert restored_snapshot.scope.transport_profile == "sales-secondary"
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_ingress_context.py -v`; expect absent business receipts/context.
- [x] **Step 3: Implement** durable pre-hook observation on direct/producer paths with one source id across replay. Capture raw typed input and the outcome separately. Use current burst batching and alias helpers; preserve transcript provenance, inbound targets, successful tool-action receipts and actual outgoing evidence. Do not infer voice text from a placeholder, or treat a held draft as already said. A new substantive input increments context, marks old candidates stale, and invalidates unused approval authorization through the account generation used in B2. Missing required facts/transcripts are explicit snapshot `missing` entries.
- [x] **Step 4: Rerun** the task file plus `scripts/run_tests.sh tests/gateway/test_whatsapp_identity.py tests/gateway/test_whatsapp_multiplex.py -v`; expect green and no change to unconfigured-profile hook ordering/fast paths.
- [x] **Step 5: Commit** the named ingress/context and seam files with `feat(gateway): record business admission decisions and conversation provenance`.

### Task A5: Dedicated tool-free composition from verified context

**Files:** Create `gateway/business/{decision,compose}.py`, `tests/gateway/business/test_compose.py`; modify `agent/auxiliary_client.py` only if a demonstrated routing gap exists, putting new behavior in a sibling.

**Interfaces:** `decision_from_result(result: dict) -> WorkDecision` and async
`compose_candidate(snapshot: ContextSnapshot, decision: WorkDecision, *, main_runtime: dict, previous: Candidate | None = None, reasons: tuple[str, ...] = ()) -> Candidate`.
It uses `async_call_llm(task="business_compose", main_runtime=..., messages=..., tools=[], timeout=60, max_tokens=2048)` and extracts visible structured content only. Errors return through the service as failed receipts, never customer prose.

- [x] **Step 1: Write** `test_failed_result_never_calls_composer`, `test_composer_receives_offer_and_verified_action`, `test_invalid_output_and_missing_context_escalate`, `test_stale_composition_is_discarded`:

```python
assert decision_from_result({"failed": True, "final_response": billing_text}).kind == "failed"
assert observed_call["tools"] == []
assert observed_call["timeout"] == 60
assert observed_call["max_tokens"] == 2048
assert candidate.completed_action_ids == (successful_removal_id,)
assert stale_result.state == "superseded"
assert invalid_result.state == "failed"
assert socket_calls == []
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_compose.py -v`; expect absent composition contract.
- [x] **Step 3: Implement** decision parsing with runtime-failure precedence and a bounded composer request using the snapshot, supplied intent, applicable rules, and placeholders for credential slots. Validate fact/action references against that snapshot, including subject and successful action state. Store model/route/usage/error metadata without raw secrets. Perform context CAS again before saving. A schema-valid but semantically suspect claim still goes through checks/reviewer/human approval; do not claim schema validation proves prose true.
- [x] **Step 4: Rerun** the task file; also call the same fake auxiliary route under two profile scopes and assert the keys/context never cross. Test a JSON scalar, truncated output, and reasoning-only output as failures.
- [x] **Step 5: Commit** the named files with `feat(gateway): compose customer messages in a dedicated auxiliary stage`.

### Task A6: Bounded checks, review, and immutable corrections

**Files:** Create `gateway/business/review.py`, `tests/gateway/business/test_review.py`; extend `store_drafts.py` for CAS-attached check/verdict records.

**Interfaces:** Async `review_candidate(snapshot: ContextSnapshot, revision: DraftRevision, *, main_runtime: dict) -> ReviewVerdict`;
async `prepare_draft(store: BusinessStore, snapshot: ContextSnapshot, decision: WorkDecision, *, policy: PolicyBundle, main_runtime: dict) -> Receipt`.
The latter owns initial compose, checks/review, and at most two rewrites; it uses A5 rather than another drafting implementation. `store.attach_review(scope, revision_id, *, verdict, expected_context_version) -> DraftRevision` rejects stale results.

- [x] **Step 1: Write** `test_two_rewrites_then_attention`, `test_reviewer_timeout_is_unavailable`, `test_policy_failure_and_late_review_do_not_authorize`:

```python
assert compose_call_count == 3  # initial plus two rewrites
assert len(saved_revisions) == 3
assert final_receipt.state == "attention"
assert unavailable.recommendation == "unavailable"
assert unavailable.error_code == "timeout"
assert required_failure_receipt.state == "blocked"
assert human_revision.review is None  # late prior-version verdict did not attach
assert approval_count == 0
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_review.py -v`; expect absent orchestration.
- [x] **Step 3: Implement** deterministic checks before advisory review, review via `business_review` with tool-free 60-second/2,048-token calls, and a state-bounded loop. Recheck every changed candidate. Preserve all originals/reasons/versions. Missing/timeout review creates attention but never an automatic approval. Queue tasks on exhausted correction budget; keep failed checks unapprovable. Distinguish context supersession from a rejected candidate.
- [x] **Step 4: Rerun** the task file; expect green, including no rewrite after a working-turn failure and no repeated state update after cancellation.
- [x] **Step 5: Commit** the named review/store/test files with `feat(gateway): check and review bounded draft revisions`.

### Task A7: Shadow gateway wiring and audience-separation contracts

**Files:** Create `gateway/business/{service,lifecycle,turns}.py`, `tests/gateway/business/{test_shadow_gateway,test_drafting_eval}.py`, `tests/fixtures/business/drafting_cases.jsonl`; extend `conftest.py` with `business_gateway`. Modify `gateway/run_turn_runner.py`, `gateway/run_turn_prepare.py`, `gateway/run_turn_persistence.py`, `gateway/run_turn_progress.py`, `gateway/run_bootstrap.py`, and `gateway/run_shutdown.py` at their existing result/lifecycle seams. Add `docs/private-fork/TRAINING_WHEELS_OPERATIONS.md` (shadow use only at this point).

**Interfaces:** `BusinessService(store, *, config_resolver, runtime_scope)` wires A1–A6;
async `handle_turn(scope: Scope, episode: EpisodeRef, result: dict, *, main_runtime: dict) -> Receipt`;
async `start_business_service(runner) -> BusinessService | None` and
`stop_business_service(runner) -> None`;
`handle_business_turn(runner, event, result: dict) -> Receipt | None` is the runner integration. `None` means unconfigured/personal mode, never an implicit fallback for a configured business failure.

For shadow-only observation, async `shadow_decision(snapshot: ContextSnapshot, legacy_result: dict, *, main_runtime: dict) -> WorkDecision` makes at most one additional tool-free `business_compose` auxiliary call with a decision-only prompt. It never changes or executes the legacy worker's instructions/tools. Supervised mode gets its decision directly from the working agent's structured result.

- [x] **Step 1: Write** `test_shadow_never_dispatches`, `test_silence_failure_and_progress_are_not_drafts`, `test_personal_turn_still_uses_existing_delivery`, and replay evaluation assertions:

```python
assert shadow_receipt.state in {"held", "attention", "failed", "no_reply", "escalate"}
assert native_socket_calls == []
assert no_reply_receipt.state == "no_reply"
assert failure_task_count == 1
assert composer_call_count_for_failure == 0
assert customer_candidates_containing_progress == []
assert personal_response == expected_legacy_response
```

- [x] **Step 2: Run** `scripts/run_tests.sh tests/gateway/business/test_shadow_gateway.py tests/gateway/business/test_drafting_eval.py -v`; expect missing lifecycle/integration behavior.
- [x] **Step 3: Implement** startup/shutdown and scoped background work, shadow observation without changing legacy dispatch, and a configured-business branch authoritative only when B7/C7 readiness allows supervised mode. For supervised working turns append the outcome schema, episode identity and current policy as new task input in `gateway/run_turn_prepare.py`, once per admitted turn; verify actual model requests preserve prior messages/system bytes. Shadow evaluation uses `shadow_decision` then the ordinary compose/review pipeline and never changes the legacy worker's live instructions. Runtime failure bypasses both shadow decision and composition. Incomplete supervised readiness holds. Persist silence/failure/escalation outside empty-response normalization; disable customer progress/voice/footer paths. Sanitize evidence: remove real contacts/logins, retain uncertainty on inferred corrections, and do not copy obsolete offers as policy. Evaluate outcome/facts/action rather than exact wording. Reproduce the precise HTTP-400 insufficient-credit envelope using existing `classify_api_error`; change the classifier only if it fails.
- [x] **Step 4: Run** the task files plus `scripts/run_tests.sh tests/gateway/test_gateway_silence_tokens.py tests/gateway/test_normalize_empty_agent_response.py -v`; expect business separation and legacy invariants green. Shadow evaluation reports quality/coverage/cost/latency, never certifies readiness for auto-send.
- [x] **Step 5: Commit** only this task's files with `feat(gateway): wire supervised drafting in send-free shadow mode`.

## Plan exit

- [x] Run `scripts/run_tests.sh tests/gateway/business/ tests/plugins/test_business_policy_contract.py -v` and `python scripts/check`; require exit 0.
- [x] Verify no live mode/queue was changed, no credentials/evidence exports were committed, and native shadow work cannot send. Publish the offline evidence in the PR description.
- [x] Begin Plan 02 only after these interfaces are stable. Do not enable a supervised live account from this plan.
