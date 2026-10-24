# Business messaging training wheels: dedicated drafting, supervised delivery

Date: 2026-10-24

Status: Approved for implementation planning on 2026-10-24 when the user invoked
`superpowers:writing-plans` after reviewing this design. Approach B and the proposed
defaults below are the planning baseline. Implementation and live rollout have not begun.

## 1. Intent and agreed direction

Sentio is a business-oriented Hermes fork. Customer messaging must have a native
supervision protocol rather than depend on prompts and a plugin that rewrites the
WhatsApp bridge after upgrades. The first consumer is the existing external
`hermes-whatsapp-sales-ops` plugin; the first transport is WhatsApp through Baileys.

The user selected **B: a dedicated customer-message drafting stage**. Hermes owns
the conversation protocol, drafting/review orchestration, approvals, pacing,
delivery evidence, and the mechanics of learning from human review. The plugin
owns CRM integration, research, campaign strategy, product facts, sales rules,
templates, and sales-specific evaluation criteria.

Success means an operator can run the sales workflow on the fork's stock bridge,
review and correct drafts, approve a batch without causing a burst, understand
which contacts still need attention, and upgrade without reapplying bridge patches.

## 2. Evidence and limits of what is known

User-supplied evidence is in the workspace's `.orca/drops/`:

- `hermes-whatsapp-gateway-upstream-fixes.md`: deployment incident brief.
- `drafting-failures.md`: exported queue evidence and annotations.
- `drafting-cases.jsonl`: 24 examples, comprising 12 recorded text replacements
  and 12 rejected-then-later-sent associations.
- `build.py`: inspected as an evidence generator, not executed against a live
  profile. Its classification is based on rejection-reason patterns and manual
  annotations; it is not an independent classification of each turn's cause.

The concrete external consumer is available at
`/home/hermes/orca/workspaces/sentio-plugins/hermes-whatsapp-sales-ops`, revision
`b914aadd6d6683265008673174fc15e117f0f21f` (clean working tree when inspected).
That path is the user-supplied local source, not a runtime installation convention.
Relevant implementation inputs are `__init__.py`, `owner_mirror.py`, `jev_triage.py`,
`bridge/send_gate.js`, `bridge/send_governor.js`, `bridge/apply_gate_patch.py`,
`bridge/apply_digest_patch.py`, `bin/sales-ops-learn`, and `bin/sales-ops-*` callers.
The configuration examples are in `examples/`; offline queue/governor behavior is
exercised in `bridge/*.test.mjs` and `tests/`. The implementation plan must pin this
consumer revision and enumerate its direct senders/scheduled writers before migration.

The legacy gate stores JSON under `queue/{pending,sent,rejected,lint_failed}` with
text/media context, approval metadata, and optional platform ids; scheduled work is
in `scheduled-releases.json`, pacing state in `governor-state.json`, configuration
in `gate.json`/`limits.json`, and verdicts in `judge.jsonl`. Build synthetic importer
fixtures from these writers/readers and their tests. The evidence export is not a
complete backup or lossless importer fixture. A read-only import dry run on the
pilot's actual backup must pass before cutover; missing formats or attachments
become operator tasks, never guessed payloads. Do not commit the raw evidence
exports: they still contain business/contact data despite password replacement.

The export reports 57 rejected records classified as internal commentary, 38 as
provider/error leaks, and 66 as reactions, notices, or other non-message cases.
These are counts within the supplied rejected records, not general failure rates.
The associated later sends do not prove that the later message corrected the
earlier draft. Approval-channel labels do not establish where drafting ran.

Three historical replacements conflict with later confirmed policy. A human edit
is evidence of an individual decision, not automatic permission to change policy.
The 453 reviewer records include 189 errors; these are verdict records, not proven
unique drafts. Aggregate sent/edited counts cannot establish reviewer agreement.

Important code observations in this checkout:

- `gateway/run_turn_persistence.py::_hmwa_shape_agent_response` starts with
  `final_response` and normally turns it into outbound text. It rejects silence
  markers when `response_filters.silence_allowed` is false. Existing history and
  tests establish that this intentionally protects addressed assistant users from
  unexplained silence; business mode needs a distinct, explicit outcome contract.
- `gateway/run_turn_persistence.py::_hmwa_deliver_turn_response` has additional
  sending paths, including voice, media, and footers. Guarding only a final string
  would be insufficient.
- `scripts/whatsapp-bridge/bridge.js` skips `messages.update` records without
  poll updates. Ordinary delivery acknowledgments need native forwarding; the
  incident brief's assertion that all required ack plumbing exists is too broad.
- `gateway/delivery_ledger.py` calls successful adapter handoff `delivered`, is
  best effort, and may recover ambiguous sends with visible prefixes. It is useful
  existing infrastructure, but cannot be the authoritative business outbox as-is.
- `agent/error_classifier.py::_classify_400` already has billing phrase rules.
  Reproduce the incident's exact provider envelope before claiming a classifier
  change is necessary.
- `gateway/whatsapp_identity.py` already resolves aliases, and
  `plugins/platforms/whatsapp/bridge_ownership.py` already resolves persisted ports.
  Extend and reuse these paths rather than implement competing identity/port rules.

## 3. Approaches considered

| Approach | Advantages | Costs and limitations | Decision |
| --- | --- | --- | --- |
| A. Existing working agent returns a structured customer-message outcome | Fewer model calls; smaller orchestration change | Operational work and customer wording still share an agent role; audience confusion remains harder to isolate | Not selected |
| B. Working agent supplies a structured decision and verified context to a dedicated composer | Separates operational output from customer copy; independently testable drafting and review | Additional latency/cost; context completeness and freshness must be enforced | Selected by the user |

Both approaches require the same native approvals, authoritative outbox, account
governor, and bridge enforcement. A send-only gate would not address the observed
drafting failures and does not satisfy this design.

## 4. Goals, exclusions, and failure conditions

Goals:

1. Customer copy is deliberately composed; free-form agent output is never
   implicitly addressed to a contact in business mode.
2. Silence, escalation, operational failure, and a message draft are distinct,
   durable outcomes.
3. Human review initially governs every external message, including scheduled
   openers, replies, follow-ups, media, edits, and tool/cron sends.
4. All Hermes-controlled transport sends obey native approval and account pacing,
   including retries, standalone senders, and recovery.
5. Human decisions and revisions produce usable, attributable learning evidence.
6. Contact history describes what was actually communicated, with transport
   acceptance and confirmed delivery kept distinct.

Excluded from the first delivery: a CRM implementation, sales/research logic,
Cloud API migration, new renderers for every platform, a new dashboard, automatic
graduation to unsupervised sending, a new provider framework, and guarantees that
an account cannot be restricted. Configured pacing is a limit Hermes enforces,
not a claim that WhatsApp considers any particular volume safe.

Unacceptable outcomes include an unreviewed alternate send path, treating a held
draft as sent, silently disabling business mode when configuration breaks,
unrecorded drops, automatic resend after an ambiguous attempt, and teaching the
agent a historical exception as a standing rule without confirmation.

## 5. Ownership and operating mode

### Approved planning defaults

Business mode is explicitly enabled per WhatsApp sending account through existing
profile/platform configuration and the managed configuration overlay. New business
setup selects supervised mode; personal profiles retain existing behavior. Enabling
the sales workflow requires supervised mode and capability validation. Activation
and changes to supervision are authenticated operator actions, never model output.

Once enabled, the bridge persists the account's supervised requirement. Missing,
invalid, or conflicting policy/configuration makes dispatch unavailable; it never
falls back to personal-mode sending. An explicit authorized mode change is audited
and cancels or revalidates outstanding work. Serving the same paired account from
competing owners is refused using existing ownership/credential-lock mechanisms.

Runtime profile and transport profile are stored separately using existing routing
identity. One authoritative gateway store owns business records in additive,
versioned `business_*` tables in the launch home's `state.db`. Rows include both
profiles, transport/account identity, contact identity, and source admission id.
The global account scheduler coordinates physical sends; queries and mutations are
authorized for the caller's profile. A routed profile cannot choose another bot,
inspect another profile's queue, or change account-wide managed limits.

Verified operator destinations are explicitly configured and alias-resolved. A
contact, model, or plugin payload cannot label its own destination as an operator
to obtain an exemption. Operators may review within their authorized profile;
reviewing messages does not grant host administration privileges. Unavailable
operator notifications remain durable tasks with visible retry/status information.

## 6. Conversation protocol and dedicated composer

### The working agent

The existing agent performs research, reads business tools, and determines the next
action. In business mode its free-form final response and commentary are internal
work products. They cannot become customer copy through normalization, progress,
footer, recovery, or error handling. Explicit tool/cron messages enter the same
business submission path rather than dispatch immediately.

The agent proposes one validated outcome for an inbound episode:

| Outcome | Required data | Gateway behavior |
| --- | --- | --- |
| `compose` | Intent, audience, referenced facts/action results, source episode | Invoke the composer |
| `no_reply` | Reason code, source episode, relevant policy reference | Record intentional silence, no customer text |
| `escalate` | Reason, context references, authorized worklist | Create a human task; no implied customer acknowledgment |
| `failed` | Structured failure class and recoverability | Retain the unresolved episode; alert the operator |

The runtime stamps operational failures; a model cannot convert `failed=True` into
a successful decision. An invalid outcome is a recorded protocol failure, not text
to send. The composer cannot run on a failed working turn just to turn its error
into polite customer copy. Partial successful tool actions remain recorded even
when the rest of the turn fails, so later recovery does not repeat them blindly.

### Context supplied to the composer

Hermes constructs a versioned context snapshot containing the correct business
identity and contact; inbound episode and relevant conversation; actual outgoing
messages and their acceptance/delivery evidence; pending offers and commitments;
known contact facts and their source; successful action receipts; current business
rules and confirmed lessons; approved templates; and the working agent's proposed
intent. Missing required context is explicit, not a blank message interpreted as a
customer's silence.

Customer, operator, tool, and platform events have distinct provenance. Owner-typed
messages become already-communicated outgoing history, not new customer prompts.
Voice-note transcripts are carried with their provenance; `[ptt received]` is never
treated as the available transcript. Reactions are linked to their target message.
Event type alone does not decide whether a response is appropriate: a reaction to
an offer may carry intent; an unrelated status notice may not.

The composer has no sending or business-mutation tools. It produces only a typed
customer-message candidate or a structured request for missing context/human help.
It may not invent product facts, permissions, deadlines, or action completion. A
missing source reference or required receipt is a validation problem; semantic
checks and human review still matter, since a schema cannot prove prose truthful.
Claims such as removal, booking, or follow-up completion require the corresponding
successful action receipt. The plugin supplies those actions and domain meaning.

Composer, reviewer, and lesson-proposal calls use the existing auxiliary-model
router, profile credential scope, hooks, usage accounting, and bounded fallbacks.
Defaults inherit the profile's model route; explicit per-role overrides are
allowed. There are no script-local model strings and no new provider stack.
Timeouts, invalid structured output, and exhausted fallbacks become typed failures.
Configuration validation probes the selected routes' required capabilities.

Confirmed lessons arrive as versioned new turn/task input. Do not rewrite prior
messages, reload memory, change toolsets, or rebuild a live conversation's system
prompt. Provider-specific structured-output support is optional; validation and
fail-closed behavior are mandatory on every supported route.

## 7. Draft checking, review, and exact approval

Each draft has immutable revisions. A revision records its context snapshot,
policy/lesson version, source inbound ids, original candidate, checks, reviewer
result, and any replacement. Default correction budget is two rewrites, followed
by explicit operator attention if still unsuitable. Every replacement is checked
and reviewed again; original failures are never overwritten.

Deterministic checks enforce runtime validity and plugin-supplied business rules.
The native framework owns running them consistently. Prices, sales tone, forbidden
claims, source requirements, and company-specific templates remain plugin policy.
Failure to load required policy blocks progression. A reviewer timeout is recorded
as `unavailable`, with a reason; it is never interpreted as approval. An operator
can inspect and explicitly override an unavailable/advisory reviewer with an audit
reason, but cannot override missing ownership, invalid recipient, or failed required
checks through an ordinary approval. Changing policy is a separate authorized act.

Reviewers return a recommendation (`approve`, `revise`, `reject`, `unavailable`),
reasons, and policy references. Initially they never authorize customer dispatch.
Operator cards show checks, reviewer availability, conversation context, and the
current revision. Invalid or failed drafts appear as attention tasks, clearly
distinguished from approvable customer-message cards.

Approval is bound to account, recipient, revision, policy version, context version,
reply target, mentions, ordered message parts, attachments, and expiry. The hash
covers the final rendered delivery plan after formatting and redaction, including
attachment contents; no later footer, rewrite, or caption substitution is allowed.
An edit creates a new revision and passes the same checks before its exact content
can be authorized. A policy change or relevant context change invalidates approval.

CLI/API, reaction, and digest controls call the same native service. A reaction is
accepted only from a verified, authorized actor on a card for that exact revision.
Digest item numbers bind to the particular digest snapshot, not the latest pending
list. Replayed/duplicate approvals are idempotent; ambiguous shorthand is refused.
Digests authorize items for scheduling; they never bypass pacing.

## 8. Episodes, stale drafts, and owed responses

Every admitted external inbound is recorded before policy hooks can consume it,
with source identity, event id, verdict, reason, and resulting episode/task id.
Normal unconfigured profiles keep their existing fast path. Business-mode drops,
silence decisions, and handoffs remain queryable even when no agent turn runs.

One active candidate exists per account/contact/episode. Existing burst batching
is reused so related messages are considered together; controls are dispatched
separately. New substantive inbound or an owner-typed reply invalidates unsent
drafts and scheduled approvals for that contact by default, creates a fresh context
revision, and requests redrafting or human review. Control acknowledgments and
delivery receipts do not themselves invalidate content. Dispatch rechecks freshness
atomically with its claim; an already attempted send is reconciled, not undone.

Episode resolution is separate from draft state. Rejection, lint failure, and
queue presence do not settle an owed response. `no_reply` intentionally resolves an
episode with its reason. Escalation remains owed to a human until explicitly
resolved. Transport acceptance is visible as `awaiting_delivery`, rather than
`delivered`; unconfirmed delivery stays visible and is not silently counted as an
answered lead. A real owner reply can settle or update an episode with its evidence.

Contact suppression is a separate native record checked at submission and dispatch.
The plugin supplies the interpretation of wrong-number/opt-out events and the CRM
mutation, while Hermes enforces the resulting do-not-contact state across all
future campaigns, cron, and reply paths for that account. Suppression invalidates
queued contact messages immediately; it is not postponed until approval of a
possible acknowledgment. Any allowed final acknowledgment needs a narrowly scoped
explicit policy exception and ordinary human approval, rather than clearing the
suppression flag. A claim that CRM removal succeeded still requires its receipt.

## 9. Durable outbox, governor, and transport backstop

### Outbox responsibilities

Separate approval/content state from transport state. A revision may be pending,
approved, rejected, superseded, or expired. An approved item may be waiting for a
window, claimed for an attempt, accepted by transport, delivered, failed, or unknown.
`held` is a durable receipt with an outbox id, not a fabricated platform message id
or `SendResult(success=True)`. Existing callers must learn explicit held/scheduled
outcomes so retry and CRM code cannot treat them as dispatch success.

The native business outbox is fail closed: inability to commit its record blocks
the send. It owns claims, attempts, approved revisions, scheduling, and recovery.
Retries retain the approved payload and recheck current policy, context, and pacing.
New text requires a new revision and approval. Only one recovery owner handles a
business item: legacy delivery sweeps recognize these records and hand off to the
outbox. They cannot independently resend or prepend a recovered-message notice.
Legacy non-business behavior remains intact.

### Configurable account governor

All external physical sends share the sending account's governor, including
multiple chunks, media, edits, reactions/polls initiated by automation, and retries.
Every physical request reserves/spends attempt capacity before socket submission;
capacity is refunded only when evidence proves no submission occurred. Delivery
confirmation is not used to defer spending capacity until too late. Logical-message
budgets may additionally be configured, but cannot replace physical-request limits.

Configuration includes IANA timezone, per-class sending windows, minimum spacing,
optional jitter, hourly/daily caps, total account caps, queue expiry, failure-spike
thresholds, and pause/resume. Unknown classes get the conservative outreach policy;
reply classification requires actual inbound provenance, not a model assertion.
Replies can have separate priority/limits but still obey global limits. Operator
notifications have a verified separate lane with bounded rate/deduplication so
alerts remain usable without creating their own flood.

The plugin's current governor settings can be explicitly imported into the first
deployment. Its South African timezone and numerical presets are not universal
defaults. Until configured and validated, business sending remains held. Invalid
windows do not mean always open. Configuration and counters survive restart;
configuration changes recompute eligibility without causing a backlog burst.

Restriction/auth signals pause dispatch and notify the operator. An authentication
revocation sets `needs_reauth` and stops automatic reconnect/respawn loops for that
account. Resume is explicit after the cause has been resolved. Ordinary transient
errors use bounded backoff while preserving account spacing. Pause invalidates any
unused dispatch authorization.

### Bridge backstop

Python owns the protocol, business state, and scheduling. The stock fork bridge
owns transport formatting, pairing, socket calls, and receipt/event forwarding.
No sales rule, composer, CRM, or lesson subprocess lives inside JavaScript.

In supervised mode every bridge endpoint capable of external communication needs
a one-use, short-lived dispatch authorization from the native service. It is bound
to the exact rendered payload, account, target, outbox attempt, and account lease
generation. The bridge authenticates the service, validates ownership and policy
generation, and records authorization consumption before its socket call. There
is only one physical send in flight per account, and the next slot is based on the
actual dispatch attempt. Transport-generated protocol traffic/receipts are not
customer-message submissions.

Old raw HTTP callers receive a typed refusal directing them to submission, not a
silent bypass. The service offers an authenticated submission API usable by the
plugin, CLI, tools, and cron, plus read/review/status operations. The caller cannot
mint authorizations, select an operator exemption, or choose a foreign profile.
Sandboxed profiles do not receive the bridge dispatch credential. If the service
is down, expiry cannot be checked, or ownership is uncertain, the bridge holds.

Across a bridge/service crash, consumed-but-unconfirmed authorizations are unknown
attempts; they cannot simply be replayed. Dispatch consumption and a compact
transport attempt journal must survive bridge restart and be reconciled to the
outbox. This is a narrow native protocol, not a second policy/queue implementation.

## 10. Delivery evidence and recovery

Persist each physical request and its platform message ids. Emit ordinary delivery
updates from the bridge, normalize them in the adapter, and reconcile out-of-order
or duplicate receipts. A receipt arriving before the HTTP send result is retained
and joined later using account and platform message id. Receipt interpretation is
validated against the pinned Baileys version during implementation, not guessed.

`accepted` means the transport accepted the request; `delivered` requires affirmative
recipient-delivery evidence. Read receipts are additional optional evidence, not a
requirement for delivered. Chunked items expose per-part state; the aggregate is
delivered only when every required part is delivered. Partial failure never causes
an unconditional replay of all parts.

For unknown recipients, preflight registration using existing alias/contact data
and `onWhatsApp` where applicable. Known-valid recent inbound provenance may satisfy
the check. A definitive negative means `failed(no_whatsapp_account)`. **Proposed
business default:** lookup error means unresolved/held with a visible reason, rather
than the old patch's fail-open behavior. Do not attempt phone lookup on an unresolved
LID by inventing a phone number.

A timeout after submission or crash during socket work is `unknown`, even if no
platform id was returned. It raises an operator reconciliation task; no automatic
duplicate or customer-facing recovery notice is generated. Late authoritative
receipts can reconcile it. A human can explicitly authorize a retry acknowledging
uncertainty, under the same checks and pacing. Missing delivery evidence does not
prove failure. Definitively unsubmitted attempts may retry automatically, bounded
by expiry/backoff/caps. Permanent failures and abandoned work remain visible tasks.

## 11. Native learning protocol

Review records retain original candidate, every automatic/human revision, checks,
reviewer verdict on its exact revision, final approval, actual accepted/delivered
payload, actor, reason, and policy/lesson/model versions. Human rejection without
a reason remains weak evidence. Associated later messages are not assigned as
corrections without an explicit revision/supersession link.

The native lesson proposer produces candidates with evidence references; only a
human confirmation activates a lesson. Keep the content and applicable business
context in the plugin/profile policy; Hermes owns storage/versioning, confirmation,
applicability, revocation, and delivery of those lessons to later tasks. A one-off
edit cannot silently override standing policy. Historical contradictions are shown
for review and excluded from unqualified training examples.

Measure reviewer results on the same immutable revision the operator reviewed.
Store unavailable/error reasons, coverage, agreement, and false approvals separately,
partitioned by message class and policy/model version. Historical aggregate counts
are not sufficient to certify readiness. Phase one has no automatic promotion;
any future selective autonomy needs a separate explicit operator policy and retains
the same checks, pacing, audit, and pause mechanisms.

## 12. Plugin integration and credentials

Use the existing plugin registry and add only contracts consumed by this sales
plugin: context/fact/action contribution, required deterministic checks, optional
review criteria, and submission/outcome APIs. Policy-bearing contributions have
validated typed results and declared failure behavior; best-effort observer hooks
cannot authorize sending. No parallel hook registry or permanently added core
model tool is required. Out-of-process/isolated plugins use serializable contracts
and authenticated service methods rather than live adapter objects.

The plugin keeps sales orchestration and CRM synchronization. It consumes durable,
cursor-addressable outcome records through the authorized API, with an idempotent
consumer key; hooks can wake it but missing a hook cannot lose an update. It submits
drafting jobs with references to facts and intent rather than raw bridge sends.
Existing `sales-ops-*` command names may become wrappers around native operations.

Credential delivery is an explicit capability required before migrating demo-login
workflows. Preserve fail-closed general egress redaction. A structured credential
reference grants release of a particular secret version only to an approved account
and recipient; formatting occurs before approval-plan binding. Operator previews
show a deliberate credential indicator. Plain logs, transcripts, reviewer prompts,
and learning examples do not receive raw secrets; the composer uses placeholders.
Rendered credentials are resolved only in the restricted delivery path. No
post-redaction password restoration or global redaction disable survives migration.
Exact secret storage/encryption integration is a builder decision using existing
secret facilities; raw customer credentials must not become ordinary queue JSON.

## 13. Configuration and operator surface

Use `config.yaml` and the existing managed overlay for behavior. Only credentials
belong in secret storage; no new non-secret `HERMES_*` environment switches.
Configuration covers supervised mode, operator identities, required policy sources,
auxiliary model routes, rewrite budget, approval expiry, governor limits/timezone,
and notification destinations. The builder selects exact additive key names and
documents how the gateway's raw config loader reads them.

Start with the existing CLI and WhatsApp operator channel. Native operations cover
queue/status, exact-revision show/edit/approve/reject, digest/re-card, pause/resume,
human tasks, delivery reconciliation, lesson confirmation, and reviewer metrics.
Machine-readable receipts report held/scheduled/accepted/delivered distinctly.
Capability checks identify unsupported bridge versions, absent policy/operator
configuration, unreachable model routes, and reauthentication requirements before
dispatch is enabled. Fallback absence is a visible readiness issue, not a claim
that only a particular provider architecture is safe. No dashboard is required
for the first usable supervised deployment.

## 14. Delivery slices and migration

These are outcome milestones, not a task-by-task implementation plan.

1. **Drafting in shadow mode.** Implement the business outcome contract, context
   provenance, dedicated composer, checks, reviewer error reporting, and native
   review records. Replay sanitized cases and new fully contextualized examples.
   Existing live supervision remains in place; native shadow mode sends nothing.
2. **Native transport authority.** Implement approvals, durable outbox, account
   governor, bridge backstop, delivery receipts, and recovery ownership. Prove all
   sending paths, failure cases, and multi-profile isolation with a fake transport
   and restart tests. No unsupervised business dispatch is enabled between slices.
3. **Complete the supervised workflow.** Add WhatsApp reactions/digests/tasks,
   confirmed lessons and reviewer metrics, structured demo credentials, native
   plugin APIs, and read-only import planning for existing pending/scheduled items.
4. **Single-account cutover.** Pause the legacy gate and governor; stop legacy
   scheduled releasers/cron writers; reconcile in-flight/unknown attempts. Back up
   queue and decision evidence. Import supported outstanding records idempotently;
   preserve originals and provenance, but require fresh native approval for every
   imported unsent item. Historical `sent` means legacy transport acceptance at
   best, never invented delivered evidence. Unsupported records become explicit
   operator tasks. Import available contact suppression before enabling dispatch;
   unresolved suppression/identity conflicts block the affected contact. Validate
   native bridge capabilities, enable supervised mode,
   point plugin wrappers at native submission, then resume at imported limits.
5. **Retire patching.** Once the supervised pilot passes the acceptance matrix,
   remove gate/digest patch application and duplicate governor/queue code from the
   external plugin. Keep a tested compatibility adapter only where an old command
   has a real caller. Extend to further accounts after the same readiness checks.

Rollback always begins by pausing dispatch. Reconcile native attempts, export
pending items, and require review before restoring the legacy workflow. Do not run
two schedulers or two queue authorities for one account. Updating to an older bridge
without the enforcement capability refuses business startup rather than bypassing
the protocol. No live deployment or customer send is authorized by this design.

## 15. Acceptance and evaluation

Deterministic contracts use real gateway/plugin imports and temporary homes, with
fake model and transport boundaries. Python tests run through `scripts/run_tests.sh`;
Node bridge contracts run in the existing Node test lane. No source-reading tests,
fixed hook counts, real customer sends, or host-profile writes are used as proof.

| Contract | Required observation |
| --- | --- |
| Provider failure | No compose call, customer draft, customer progress, or transport attempt; one deduplicated durable operator task |
| Intentional silence | Recorded `no_reply`; no normalization fallback and no customer text, including crash recovery |
| Audience separation | Working-agent final/progress text never becomes customer copy; malformed protocol result is held |
| Context | Prior offer, known details, owner reply, reaction target, and available voice transcript reach the right composer snapshot |
| Action evidence | Missing/failed removal or booking receipt cannot validate a completion claim |
| Required checks | Plugin check failure/unavailability blocks all paths, including tool, cron, standalone, media, and edit |
| Reviewer failure | Explicit unavailable reason; no automatic approval; operator escalation/override remains auditable |
| Exact approval | Edited text, changed attachments, stale card/digest, changed policy, wrong actor, or foreign profile cannot dispatch |
| Freshness | New substantive inbound/owner reply invalidates unsent scheduled work before its dispatch claim |
| Contact suppression | Opt-out/wrong-number state stops pending and future campaign sends; approved acknowledgment does not reopen contact eligibility |
| Pacing | Concurrent approvals, multiple chunks, retries, restart, and cap/window changes never exceed configured physical-send limits |
| Bridge enforcement | Every supervised raw endpoint rejects absent, forged, stale, foreign, or already-consumed authorization |
| Recovery | Crashes before/after authorization consumption yield safe pending/unknown states; legacy sweeps cannot duplicate business dispatch |
| Delivery | Negative registration check fails; lookup error holds; early/out-of-order/duplicate receipts reconcile; partial delivery stays partial |
| Learning | Only confirmed lessons activate; versioned policy conflict and weak association do not silently become training truth |
| Migration | Import repeated twice is idempotent; no imported approval is live; unsupported items remain visible; rollback is paused |

Semantic quality is evaluated separately against sanitized, contextualized examples
from the supplied batch. Expected outcomes include audience, relevant facts, action
choice, prohibited claims, and next-step completeness; do not require exact human
wording or copy obsolete offers. Incomplete historical contexts may correctly
require escalation. Report quality, coverage, cost, and latency by model/policy
version. Passing a schema/transport test is not proof that drafting quality is solved.

The first live pilot requires user authorization after deterministic checks pass,
using operator-owned/test recipients and supervised review. Broader live rollout
requires review of those receipts and evaluation results; no automatic graduation.

## 16. Approved defaults and builder discretion

The user has chosen the dedicated composer and the fork/plugin ownership boundary.
The following defaults are the approved implementation-planning baseline:

- WhatsApp-first shared gateway service; CLI and WhatsApp controls before a dashboard.
- Supervised external sends initially; two bounded rewrites; no automatic promotion.
- Broken required policy holds; reviewer outage permits only explicit audited human
  override of advisory review after required checks pass.
- New relevant contact input invalidates pending approvals; imported approvals reset.
- Registration lookup errors hold; ambiguous attempts require reconciliation.
- Account-wide physical-request pacing with no universal claim of safe volumes.
- Business records in the gateway-owned shared store, with strict profile ownership.
- Structured credential support is required before migrating demo-login delivery.

The builder chooses exact module/table/config names, internal wire encoding and
authentication mechanism, existing secret-store integration, schema migrations,
indexes/retention limits, UI wording, bounded model-call timeouts and token budgets,
and detailed test placement. Those choices must preserve the contracts above.
Numerical operational limits are imported or configured for the account, not guessed
from the evidence. If implementation uncovers an incompatible platform capability,
bring the resulting scope/behavior change back for a decision before changing it.
