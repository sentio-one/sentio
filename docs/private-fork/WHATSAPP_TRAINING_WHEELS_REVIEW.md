# Native WhatsApp training wheels implementation review

Reviewed on 2026-10-25 at fork commit `49bea851ade3a07a4f2bc7c70f3885effb7f9658`
against base `8a75a6168e6f47a64e4308bf9454d1f8658b0b1d`, the
[design](WHATSAPP_TRAINING_WHEELS_DESIGN.md), and all three linked implementation
plans. The external plugin checkout was reviewed at `ba177a5` on
`native-training-wheels`.

**Verdict: not ready to merge or pilot.** There is substantial component code,
including scoped SQLite records, revision checks, rendering, and lesson storage.
The production paths connecting these components are incomplete. Checked plan
steps and green component suites do not establish the claimed complete workflow.
This review supersedes the earlier completion/certification claims; it does not
change the approved design or authorize deployment, cutover, or sending.

## Blocking findings

### R1 — Supervised configuration disables the service at startup (P1)

`gateway/business/lifecycle.py:47` calls
`load_business_config(raw, supervised_latch=False)` for every configuration.
`gateway/business/config.py:156` rejects `mode: supervised` with that argument.
The exception handler clears both service and store and returns `None`, rather
than retaining a held business account. Startup never reads the persisted account
latch. The service fallback in `gateway/business/service.py:62` repeats the same
argument.

A temporary-home call to `start_business_service()` with supervised configuration
produced `supervised_latch_required`, `_business_service=None`, and
`_business_store=None`. Business-mode detection consequently cannot enforce the
requested mode. Resolve the durable account latch during startup and retain a
held state when supervision cannot start. Cover the actual startup path, including
restart and routed profiles. Owning tasks: A7/B7.

### R2 — Physical WhatsApp send routes never enter native supervision (P1)

`scripts/whatsapp-bridge/bridge.js:866` still handles `/send` by formatting and
chunking caller text and calling `sendWithTimeout()` at line 886. It does not
consult native mode, approval, or governor state. The other physical send routes
likewise retain their ordinary execution. The bridge never imports
`business_dispatch.js`, exposes no native dispatch endpoint, and advertises no
business dispatch capability in `/health`.

The new adapter changes add inbound metadata, not supervised outbound submission.
Thus the new standalone authorization helper does not enforce the running bridge's
send contract. Connect each adapter/tool/cron/standalone path and every physical
bridge endpoint to the account authority, with real endpoint tests using a fake
socket. This is an unfinished approval/pacing feature, not a demonstrated breach
of the OS or caller-allowlist boundary. Owning tasks: B4/B5/B7.

### R3 — Normal agent results cannot supply a supervised decision (P1)

`gateway/business/turns.py:155` asks the agent to finish with JSON text. The real
turn finalizer returns that text in `final_response`, but
`gateway/business/decision.py:29` accepts only a separate `decision` object or
top-level decision fields. JSON in `final_response` becomes
`failed/unrecognized_result`. The prompt's example also omits the required
`policy_version` field.

An actual call to `decision_from_result({"final_response": json.dumps(decision)})`
returned `kind="failed"` for a complete valid decision object. Define and wire one
result contract through the actual finalizer and business service; test normal
agent-shaped results rather than injecting a `decision` key in fixtures. Owning
tasks: A5/A7.

### R4 — Reviewed drafts do not reach the outbox; passing checks are rejected (P1)

`gateway/business/review.py:161` returns a fresh random `held` receipt after an
approving review without rendering/saving a delivery plan or submitting an outbox
row. `BusinessService.handle_turn()` returns that receipt unchanged. A real
composed draft therefore has no dispatchable queue item.

Separately, `gateway/business/store_outbox.py:55` and line 128 test
`checks.get("passed", False)`. The actual `CheckReport` field and persisted
serializer use `ok`. Attaching `CheckReport(True, (), "v1")` to a real temporary
store revision and submitting it raised `BusinessProtocolError("checks_failed")`.
Connect reviewed-draft promotion to the immutable plan/outbox and use the shared
check schema throughout. Test a checked draft from composition through approval.
Owning tasks: A6/B1/B2/B5.

### R5 — Operator CLI and control operations are disconnected (P1)

`hermes_cli/business_messages.py:65` unconditionally returns zero. Queue, show,
approve, and reject neither issue requests nor print results. Calling it with an
approval for a nonexistent revision returned exit code 0 and empty output.

`gateway/business/lifecycle.py:72` looks for `runner._control_server`; bootstrap
stores the server as `runner.session_control_server`
(`gateway/run_bootstrap.py:373`). Even after that registration is connected,
`gateway/business/control.py` implements only ping, consume, and invalidate, not
the advertised operator operation table. The operator client also calls the
synchronous keyword-only control client with incompatible positional arguments.
WhatsApp operator handling has no production caller either.

Wire CLI and both WhatsApp command guards to the authenticated operation table,
with truthful exit codes and results. Test real parser-to-control requests and
operator commands during an active turn. Owning tasks: B4/C1/C5/C6.

### R6 — Actual plugin requests crash; the tested alternative simulates acceptance (P1)

`hermes_cli/plugins_business_client.py:28` constructs
`Principal(capabilities=...)`, while the dataclass field is `operations`.
The actual `PluginContext.business_request()` path raises a `TypeError` before
authorization. Existing tests pass a preconstructed principal and avoid this path.

Even on that alternate path, lines 43–59 explicitly return simulated accepted/held
responses and empty events. They do not persist jobs, facts, actions, or consumer
cursors. In the external plugin, `bin/sales-ops-outreach:415` still posts to the
legacy `/send` endpoint; adding native client/job modules has not migrated that
caller. The native client's missing-context fallback also fabricates acceptance.

Use the real scoped plugin context and isolated host, persist native requests and
outcomes, and migrate the actual outreach/reply/followup callers. Assert durable
state and CRM writeback rather than only returned dictionaries. Owning tasks:
C1/C4/C6.

### R7 — Dispatch has no worker, uses an incompatible client, and strands held work (P1)

`gateway/business/transport.py:132` defines `dispatch_due()` without a production
caller or lifecycle worker. Its native branch at line 250 calls
`await request_business("dispatch", req)`. The actual function is synchronous and
has signature `request_business(home, *, operation, params, ...)`; that call
immediately raises `TypeError`. There is no matching control dispatch operation or
bridge dispatch endpoint.

Dispatch also claims an attempt before reserving governor eligibility, then
returns on governor denial without releasing or rescheduling the claim. Ordinary
window/gap denials can strand the queue item. Missing governor configuration
falls back to an unrestricted default rather than holding.

Wire a profile/account-bound worker and one coherent control/bridge protocol.
Preserve retryable eligibility when held by pacing, and require an explicit
governor. Test native execution, delayed eligibility, restart, and partial sends
through real components. Owning tasks: B3/B4/B5/B7.

### R8 — Real acknowledgments never update native delivery state (P1)

`scripts/whatsapp-bridge/bridge.js:483` enqueues `deliveryReceipt` objects, but
`plugins/platforms/whatsapp/adapter.py:1027` has no receipt consumer. It builds
ordinary message events from those records with empty bodies.
`apply_delivery_event()` has no production caller.

The account keys also disagree: ingress uses the receiving transport profile for
both transport and account identity (`gateway/business/ingress.py:68`); the bridge
emits literal transport profile `whatsapp` and the paired phone number as account
ID (`bridge.js:477`). Simply adding the consumer will not join those keys.

Route typed receipt events separately from customer messages, bind them to the
canonical receiving account, and invoke durable reconciliation. Test real bridge
polling through adapter handling with early/late receipts and a secondary profile.
Owning tasks: B6/B7/C4.

## Other material gaps

- **Composer context (P2):** `gateway/business/compose.py:94` sends intent,
  referenced facts/actions, and rules, but omits conversation events, scope/contact
  identity, and confirmed lessons. The reviewer input also omits conversation
  context. This falls short of the dedicated drafting context in design §6.
- **Readiness evidence (P2):** `gateway/business/workflow_readiness.py:161`
  reports fixed latency `240.0` and agreement `0.96` as evidence. Those are not
  measured values. Readiness mostly checks configuration presence and assumptions;
  disconnected bridge/control/plugin paths are not established by those checks.
  Report unavailable metrics honestly and probe real capabilities before claiming
  readiness.
- **Acceptance coverage:** `test_full_acceptance.py` manually appends transition
  names and send timestamps to `FakeEndToEnd`. It does not invoke real approval or
  physical dispatch. Its stock-bridge upgrade object always returns success.
  `test_egress_matrix.py` asserts empty lists on a transport spy never connected to
  the runner. Sales integration tests similarly use an uncalled outage spy and a
  local fake CRM. Replace these with the actual-plugin/fake-network-boundary
  acceptance harness required by C7.

## Verification performed

All runs used offline fixtures or temporary homes. No live WhatsApp account,
production configuration, queue, or deployment was changed.

| Check | Fresh result |
| --- | --- |
| `scripts/run_tests.sh` over business tests plus the five C7 plugin/CLI/vault files | 27 files, 100 passed |
| C7's listed existing regression files via `scripts/run_tests.sh` | 12 files, 346 passed |
| `node --test scripts/whatsapp-bridge/*.test.mjs` | 32 passed |
| `python3 scripts/check` with pinned Ruff 0.15.22 on PATH | 12 checks OK, zero blocking findings, one profile-thread advisory |
| Plugin `tests/run_native.sh --hermes-root <fork>` at `ba177a5` | Exit 1 on two runs; the six native unit tests pass, but legacy product-scan tests have four failures/four errors and setup tests have three failures |
| Temporary-home production-function probes | Reproduced startup disablement, JSON decision rejection, CLI false success, plugin/client signature errors, and passing-check rejection |

The plugin wrapper used its temporary profile and pinned pre-native bridge archive.
Setup failures explicitly report missing Hermes CLI/stock bridge prerequisites;
product-scan subprocesses fail to create expected pages. These results are not
attributed to a new regression without a same-environment baseline comparison.
They do mean the full-wrapper success claim was not reproduced here.

The lint advisory is the raw policy worker thread at
`gateway/business/policy.py:76`, which does not preserve the caller's profile
ContextVars. It needs a profile-scope audit alongside routed-account integration.

## Required before promotion from draft

Resolve R1–R8, replace simulated acceptance assertions with real integration
coverage, and rerun the named suites. Correct measured readiness evidence and
verify the actual external-plugin branch together with the fork. Rebase onto
current `main` before merge; the reviewed branch was four commits behind
`origin/main` at review time. A live pilot and legacy-patcher retirement remain
separate, explicitly authorized checkpoints.

Considered and set aside: shadow mode intentionally retains legacy delivery;
absence of live deployment authorization is appropriate; this review does not
claim a demonstrated OS-isolation or unauthorized external-caller vulnerability.
Runtime corrections remain unimplemented by this review-only follow-up commit.

## Repair status (2026-10-25)

R1–R8 and the "other material gaps" are repaired in the working tree that follows
commit `d349892b88`; see `2026-10-25-training-wheels-repairs.md` for the ownership
plan. The simulated acceptance and egress tests are replaced by
`test_full_acceptance.py` (real service → operator edit → exact-digest approval →
paced dispatch → receipt reconciliation → lesson context, plus invalidation by a
newer inbound message) and `test_egress_matrix.py` (real adapter send paths with a
bridge session that fails on any call). Only the model, the policy plugin and the
network boundary are faked.

| Check | Result |
| --- | --- |
| Business, plugin, CLI and vault suites | 34 files, 138 passed, 1 skipped |
| Named C7 regression files | 12 files, 346 passed |
| `node --test` in `scripts/whatsapp-bridge` | 33 passed |
| Focused WhatsApp inbound/busy-routing and transport suites | 6 files, 63 passed, 1 skipped |
| Focused follow-up regressions | 3 files, 20 passed, 1 skipped |
| `python3 scripts/check` with pinned Ruff 0.15.22 | 12 checks OK, zero blocking findings |
| Plugin `tests/run_native.sh` on `native-training-wheels` | exit 0; all native, bridge and legacy suites pass |

The skipped test is `test_native_adapter_bridge_and_authenticated_consumption`: it
needs PM-managed Node, which this host lacks. Its endpoints are covered by
`business_endpoints.test.mjs`. `run_native.sh` now uses an exec shim instead of a
symlink for `python3`, because a venv interpreter reached through a symlink loses
its site-packages (this caused the earlier wrapper failures).

### Independent post-repair review

A second reviewer read the repaired diff. Fixed in this change:

- Latched bridge routes matched `/send` case- and slash-sensitively, so `/SEND` or
  `/send/` bypassed the latch. Matching is now normalised; the endpoint test covers
  the variants and fails without the fix.
- A `/messages` message from anyone who was not a verified operator, or with no
  service configured, was dropped before ingress recording. It now falls through as
  an ordinary inbound message.

Follow-up review findings (resolved in the current branch; evidence below):

3. **Advisory review override. Fixed.** Approval for `unavailable`, `revise`, or
   `reject` requires a nonempty override reason persisted in the approval record.
   Covered by `test_advisory_unavailable_requires_audited_approval_override`.
4. **Registration preflight. Fixed.** Cold external submissions use the owning
   bridge's registration endpoint. Negative results fail; lookup errors remain held.
   The bridge endpoint test covers registered and unregistered results.
5. **Pre-socket failures. Fixed.** Quote and attachment validation now runs before
   one-use authorization is consumed. The endpoint test proves a missing quote
   returns `submitted: false` and leaves the authorization unconsumed.
6. **Restart recovery. Fixed.** Completed attempts are not replayed from the journal.
   A leftover governor reservation is finalized once; repeated recovery does not
   duplicate history. Covered by `test_recovery_does_not_replay_finished_attempt_or_governor_history`.
7. **Queue head blocking. Fixed.** A future-scheduled or unresolved item blocks its
   own contact while other approved contacts remain claimable. Covered by
   `test_future_paced_contact_does_not_block_another_contact`.
8. **Outbound scope. Fixed and traced.** Persisted attempt context retains the
   serving runtime profile while adapter ownership resolves by transport profile.
   The secondary-profile transport test asserts the physical request scope.
9. **WhatsApp operator events. Fixed.** `/messages recard` sends an exact-revision
   card with its durable digest and plan binding; the inbound path routes authorized
   thumbs-up reactions and explicit digest approvals through `handle_operator_event`.
   Covered by the operator card persistence, reaction, and digest-command tests.

The repaired branch is ready for code review as an offline implementation. This
does not authorize a live pilot, legacy cutover, or customer sends. No live
configuration, queue, deployment, or customer messaging was changed.
