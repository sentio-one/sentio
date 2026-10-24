# WhatsApp Training Wheels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sales plugin's bridge patches with native dedicated drafting, human supervision, paced delivery, and a traceable learning loop.

**Architecture:** A shared gateway business service owns durable episodes, revisions, approvals, and account dispatch. A tool-free auxiliary composer produces customer copy from verified context; the stock WhatsApp bridge accepts only native dispatch authorizations in supervised mode. The external plugin supplies business facts/rules and consumes durable outcomes.

**Tech Stack:** Existing Python/dataclasses/SQLite/asyncio, Hermes auxiliary model router and plugin host, Node ES modules/Express/Baileys `7.0.0-rc13`, standard-library HMAC, existing encrypted vault. No dependency additions are planned.

**Spec:** [WHATSAPP_TRAINING_WHEELS_DESIGN.md](WHATSAPP_TRAINING_WHEELS_DESIGN.md), all 16 sections.

## Global Constraints

- The approved choice is B: a dedicated customer-message composer; the working agent's prose is internal in business mode.
- WhatsApp/Baileys is the first transport. CRM, research, campaigns, product facts, sales rules, templates, and sales-specific criteria remain in the external plugin.
- Every external message initially requires human approval. The rewrite budget is **two rewrites**. No automatic graduation is implemented.
- Required-policy failure holds; reviewer failure is `unavailable`, never approval. An audited human override cannot bypass required checks, ownership, or recipient validation.
- New relevant inbound/owner replies invalidate unsent approvals. Imported approvals reset. Lookup errors hold; ambiguous attempts require reconciliation.
- One account-wide governor counts **physical requests**, including chunks and retries. No universal numerical sending rate is represented as safe.
- Shared `business_*` tables live in the launch home's `state.db`; every access checks runtime/transport profile and account ownership. Bind scope explicitly in auxiliary calls, callbacks, timers, and thread hops.
- Prompt prefixes stay stable: confirmed lessons are versioned new task input, never mutations of prior messages, toolsets, memory, or a live system prompt.
- Behavior configuration goes in YAML through existing managed-overlay readers. No new non-secret `HERMES_*` switches. No new permanent core model tool.
- Extend existing alias, port, hook, vault, and auxiliary-model paths. Behavior lives in focused modules, not facade bodies. New functions CC <= 20; files <= 2,000 lines; existing ratchets may only go down.
- Python tests run only through `scripts/run_tests.sh`; use temporary homes and real imports. Node tests execute behavior, never read source text. Do not check in the raw `.orca` evidence.
- No customer send, deployment, automatic mode activation, or migration of a live queue is authorized by plan execution. Tests use fake transports; live pilot authorization is a later checkpoint.

## Review Focus

- **A late composer/reviewer result after a new inbound or human edit** must not replace a newer revision or resurrect approval — test in A5/A6 and B2.
- **An account alias or routing/profile change across restart** must not merge two accounts, lose a suppression, or select the default bot — test in A2/A4 and B7.
- **DST transitions, overnight windows, and backward wall-clock movement** must not open a closed window or release a burst — test in B3.
- **Partial multi-part sends with a receipt before the HTTP response** must reconcile each part without replaying accepted parts — test in B5/B6.
- **Malformed/oversized control requests, ambiguous digest commands, and rotated credentials** must refuse safely without changing another revision or exposing a secret — test in B4, C1, and C3.

---

## Delivery order and independently usable results

The spec spans three subsystems. Use three linked plans, in this order, rather
than one large implementation branch:

| Plan | Tasks | Independently testable result | Activation boundary |
| --- | --- | --- | --- |
| [01 — Drafting](2026-10-24-training-wheels-01-drafting.md) | A1–A7 | Native shadow drafts, context/policy checks, revisions, failures and human tasks | Sends nothing; existing live gate remains responsible |
| [02 — Transport](2026-10-24-training-wheels-02-transport.md) | B1–B7 | Exact approval, enforced outbox/governor, bridge authorization and delivery/recovery | Stock bridge exercised against fake transport; supervised live cutover remains disabled |
| [03 — Workflow and migration](2026-10-24-training-wheels-03-workflow.md) | C1–C7 | Operator controls, learning, protected credentials, native plugin wrappers, importer and cutover/rollback runbook | Readiness can be demonstrated offline; live pilot requires separate authorization |

Each task has its own red/green cycle and commit. Each plan can be reviewed and
merged with its feature disabled; rebasing onto current `main` is required before
merging. Follow the fork's PR/merge-commit workflow; no pushes or merges are part of
these steps. Do not let a partially completed plan enable a sending account.

## Workspace and source prerequisites

- Execute in an isolated checkout using `superpowers:using-git-worktrees`. The current
  workspace is already a linked worktree; do not make another unnecessarily.
- Keep the unrelated existing `package-lock.json` modification and `.orca/` files
  out of implementation commits.
- The plugin source is `/home/hermes/orca/workspaces/sentio-plugins/hermes-whatsapp-sales-ops`
  at clean revision `b914aadd6d6683265008673174fc15e117f0f21f`. Obtain an isolated plugin
  checkout for tasks that change it; `PLUGIN` in child plans means that checkout,
  not a live profile installation. Verify that baseline before the first edit;
  later plugin tasks inherit the earlier tasks' commits, rather than resetting it.
  In shell commands, `sales_plugin_worktree` is the absolute path returned by that
  isolated-checkout setup; never point it at a deployed plugin directory.
- Read the root, `gateway/`, `agent/`, `plugins/`, `hermes_cli/`, `tools/`, `cron/`,
  and `tests/AGENTS.md` as their paths are touched. This plan directory follows the
  fork's existing documents because `.gitignore` ignores `docs/superpowers/*`.
- Prepare the PM-owned test interpreter as described in `tests/AGENTS.md`. In a
  prepared shell, `python scripts/check` is the blocking CI lint command. Use the
  existing lockfile and do not mutate environments with raw pip/uv.

- [ ] After the user reviews the plans and chooses execution, make a documentation
  commit containing this index, the approved design and the three linked plans
  before task implementation or transferring work into another checkout. Stage
  those five exact paths only; use `git commit -m "docs: plan native WhatsApp training wheels"`.
  This is an execution prerequisite, not an instruction to commit during planning.

## Shared interface decisions

These names and defaults are decisions made while planning within spec §16's
builder discretion. If the code contradicts an assumed seam, preserve the contract
and update all affected plans before implementation, rather than creating a second API.

### Modules and types

New Python code lives in `gateway/business/`, with an empty `__init__.py`, focused
topic modules, and mirrored tests in `tests/gateway/business/`. `service.py` wires
components; it does not accumulate their implementation. The main `GatewayRunner`
facade only wires lifecycle/mixin entry points.

`models.py` (A1) defines frozen dataclasses and explicit JSON parsers:

- `Scope(runtime_profile: str, transport_profile: str, account_id: str, contact_id: str)`.
- `Principal(subject: str, runtime_profile: str, operations: frozenset[str], kind: str)`;
  created only from verified runtime/transport credentials, never caller JSON.
- `EpisodeRef(id: str, context_version: int)`.
- `Fact(id: str, text: str, source: str, version: str)`.
- `ActionReceipt(id: str, kind: str, subject: str, state: str, proof_ref: str)`.
- `ContextEvent(id: str, kind: str, actor: str, text: str, target_id: str | None, evidence: str)`.
- `WorkDecision(kind: str, reason: str, intent: str, fact_ids: tuple[str, ...], action_ids: tuple[str, ...], policy_version: str)`;
  kind is exactly `compose | no_reply | escalate | failed`.
- `ContextSnapshot(id: str, scope: Scope, episode: EpisodeRef, policy_version: str, lesson_version: int, events: tuple[ContextEvent, ...], facts: tuple[Fact, ...], actions: tuple[ActionReceipt, ...], rules: tuple[str, ...], review_criteria: tuple[str, ...], confirmed_lessons: tuple[str, ...], missing: tuple[str, ...])`.
- `Candidate(kind: str, text: str, data: dict, fact_ids: tuple[str, ...], completed_action_ids: tuple[str, ...], credential_slots: tuple[str, ...])`;
  kind is `text | media | edit | reaction | poll | location`. `data` is schema-checked per kind and contains attachment handles, never arbitrary host paths or secrets.
- `CheckReport(ok: bool, issues: tuple[dict, ...], policy_version: str)`.
- `ReviewVerdict(recommendation: str, reasons: tuple[str, ...], policy_refs: tuple[str, ...], error_code: str | None)`;
  recommendation is `approve | revise | reject | unavailable`.
- `DraftRevision(id: str, scope: Scope, episode: EpisodeRef, number: int, candidate: Candidate, policy_version: str, lesson_version: int, checks: CheckReport | None, review: ReviewVerdict | None)`.
- `Receipt(id: str, state: str, revision_id: str | None, reason: str | None)`;
  this is a business receipt, not a platform message id.

Use integer UTC milliseconds in persistent scheduling/receipt timestamps; IANA
timezones affect windows and calendar-day buckets. Monotonic elapsed time protects
in-process spacing. Every persisted record has `schema_version=1`; later migrations
are additive and preserve records on replay. UUID4 identifies native objects;
external idempotency keys are unique within the authenticated profile/account.

Plan B defines `DeliveryPlan`, `DeliveryPart`, `Approval`, `Attempt`, `Eligibility`,
and `DeliveryEvent` in the owning modules. Plan C defines `Lesson` and import
records there. Do not pre-create unused tables or types in A1.

### Configuration and bounded work

Add `platforms.whatsapp.extra.business` with `mode: off | shadow | supervised`
(default `off`), `required_policies: []`, `operators: []`, `rewrite_attempts: 2`,
`approval_ttl_seconds: 86400`, `queue_ttl_seconds: 604800`, and `governor: null`.
No governor configuration means **held**, not unrestricted sending. Supervised
activation also needs the durable account latch and readiness from B7/C7.

Add auxiliary keys `business_compose`, `business_review`, `business_lessons` using
the existing `_aux` defaults: provider `auto`, model `""`, timeout `60`; each new
call sets `tools=[]`, `max_tokens=2048`. Confirmed lessons are task input. Reuse
existing provider/credential fallback and usage hooks; no fallback string is baked
into scripts. One initial composition plus at most **two** revision calls is allowed.
Required plugin-policy callbacks have a `10` second deadline. A deadline or missing
required callback is a blocking report, never an empty success list.
An active business mode with no configured required policy reports
`required_policy_unconfigured`; the empty default is for disabled profiles only.

Authorizations expire after `30` seconds and use protocol `business_dispatch=1`.
Account leases have a generation/fence; expiry never automatically converts an
ambiguous in-flight attempt into retryable work. Explicit operator notification
defaults are `2` seconds minimum spacing, `10` per minute, `60` per hour, with
durable coalescing by incident id. These are operator-channel resource limits,
not recommended WhatsApp outreach rates.

### Test conventions shared by tasks

Task A1 creates `tests/gateway/business/conftest.py` with temporary launch/profile
homes, a controllable UTC/monotonic clock, and immutable scope/principal factories.
Later tasks extend that fixture only with the real components they introduce and
fake auxiliary/network boundaries. A fixture must not implement an alternate
business policy or state machine. Tests shown in the plans name assertions; build
their setup from those fixtures and the declared production interfaces.

`business_gateway` is introduced in A7 as a real configured gateway harness exposing
`receive(event)`, `finish(result)`, `restart()`, and access to `store`, `aux_calls`,
`socket_calls`, and `operator_tasks`. Its receive method waits for durable acceptance;
its finish method awaits the real worker's terminal receipt. `business_account` is
introduced in B2 as a service/store/render harness exposing `submit(candidate)`,
`approve(revision_id, **changes)`, `dispatch_due()`, `receive(event)`, `restart()`,
and real component properties. Extend these in the task using them; do not mock
the entry point whose contract is being tested.

Run each task's named file with `scripts/run_tests.sh <file> -v`, observe the specific
assertion fail on the pre-change code, then implement and rerun. For tests of brand
new modules, an initial import failure is expected; still require the behavioral
assertions to run green after implementation. Use events/controlled clocks, not
nonzero sleeps or fake host operating systems.

For the external plugin, A3 adds `tests/run_native.sh --hermes-root PATH`. From the
fork root, run `bash "$sales_plugin_worktree/tests/run_native.sh" --hermes-root "$PWD"`.
The wrapper creates/cleans a temporary home/profile and generic required config,
strips inherited provider credentials, runs the new native unittest files and
existing offline suites, and never falls back to a live profile/bridge. Its
legacy-suite bridge source is pinned to pre-native fork commit
`8a75a6168e6f47a64e4308bf9454d1f8658b0b1d`, extracted read-only into temporary storage;
native integration uses current fork source. Record baseline failures separately
rather than hiding them or fixing unrelated behavior.

## Coverage and final self-review

| Spec sections | Owning tasks |
| --- | --- |
| 1–4: intent, evidence, chosen approach, boundaries | A1/A7, C4/C7; sanitized evaluation only |
| 5: operating mode, store ownership, operators | A1/A2, B4/B7, C1 |
| 6: decisions, context, composer, scope/cache | A3–A7 |
| 7: checks, revisions, reviewer, exact approval | A5/A6, B1/B2, C1 |
| 8: admissions, stale work, suppression, owed responses | A2/A4/A7, B2/B6 |
| 9: outbox, governor, bridge, retry owner | B1–B7 |
| 10: registration, receipts, uncertainty | B5/B6 |
| 11: review evidence, lessons, evaluation | A6, C2 |
| 12: plugin contracts, protected credentials | A3, C3/C4 |
| 13: YAML, controls, readiness, status | A1, B7, C1/C7 |
| 14: migration, cutover, retirement, rollback | C4–C7 |
| 15: acceptance/evaluation | Every task; cross-path matrix in B7/C7 |
| 16: builder choices | Locked interfaces/defaults above; individual task signatures |

The planning self-review must verify all task dependencies and exported names,
five Review Focus tests, both repositories' file paths, and all acceptance rows.
No product code or tests are written by this planning change. Commit steps below
belong to execution. A live pilot and patch retirement are deliberately separate
from offline implementation success.

Planning self-review completed on 2026-10-24: all 16 spec sections map to tasks;
21 tasks have file/interface definitions and five-step red/green/commit cycles;
the five Review Focus conditions have owning tests. Cross-document links, existing
source/regression paths and shared names were checked. Runtime tests are prescribed
for execution, not reported as having passed by this documentation-only change.
