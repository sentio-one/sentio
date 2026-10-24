# Sentio catalog: plugins and skills per company (plan)

Status: **plan, nothing built yet.** Written 2026-10-24 from a read of all 765 entries in `plugin-catalog/` and the 19 bundled
plugin directories. Decisions are the owner's (marked **Decided**); everything else is a recommendation.

## 1. Why

A plugin runs inside the gateway process with the gateway's authority; it is not in the terminal sandbox. A desktop half runs in
the app's renderer with full app authority. So "which plugins a company gets" is a security control, not tidiness. Today nothing
limits it: the fork's `plugin-catalog/` carries upstream's 765 pinned entries, and `hermes plugins search/install` also fetches
Nous's **live** catalog (`LIVE_CATALOG_URL` in `hermes_cli/plugin_catalog.py`), so a company gateway can install entries that never
passed through this fork.

## 2. Design

**Decided:** admins may pick from all 765 entries (the operator is the admin of every pilot gateway). So the universe is not
pruned, and upstream's entry files are **not deleted** (that would conflict on every upstream merge).

1. **Universe** = the in-tree `plugin-catalog/` (765) + our own entries in `sentio-catalog/entries/*.yaml` (same pinned-SHA format,
   same admission rules). The live upstream fetch is **off for company gateways**.
2. **Pack** = a named file `sentio-catalog/packs/<name>.yaml`: plugins (catalog names), bundled plugins to enable, skills (feeds
   `skills.company_baseline`, D16), settings the pack implies (for example `web.search_backend`). A company is created with
   **`PACK=<name>`** in its Dokploy environment (**Decided**), pinned in the managed config so a user or agent cannot edit it.
3. **Admin allowlist** = extra names the company's admin adds on top of the pack, from the universe. Stored admin-side (launch home,
   like `profile_key_sharing.json`), never in a profile's own files. Anything off the universe needs an explicit, logged override.
4. **Enforcement** at install, enable and load for a company gateway: effective set = pack ∪ admin allowlist; anything else is
   refused and logged. `hermes plugins validate` and `hermes-plugin-guard`-style static review stay the admission gate for new
   entries.
5. Profile templates (SANDBOX_PLAN phase 6) take their skills and plugins from the company's pack.

Build order: (a) universe filter + live fetch off + enforcement, (b) pack files and `PACK=`, (c) admin allowlist and its UI.

## 3. First pack: `tendering` (tendering companies, document-heavy business work)

### Include (the pack)
| Area | Entries | Why |
|---|---|---|
| Delegation and work | `advanced-delegation` [official], `kanban` (bundled), `crew`, `hermes-teammates`, `hermes-workflows`, `deferred-questions`, `heartbeat2` | **Decided** (delegation, kanban). Tender work is multi-step and parallel: requirements, pricing, compliance, forms. `crew` turns one message into verified kanban work. |
| Housekeeping | `disk-cleanup` (bundled) | **Decided.** |
| Search | `tinyfish` [verified] as the **default** `web_search`/`web_extract` backend | **Decided** (free). See risk R1. |
| Memory | `hindsight` (+ `hermes-memory-ui` read-only viewer) | **Decided** (the owner self-hosts it and likes it). See risk R2. |
| Documents | `doc-markdown` (MarkItDown: PDF/DOCX/XLSX/PPTX to Markdown), `pdf-surgeon`, `wolfmarkdown`, `artifacts` (private library of finished documents), `redaction-pack`, `image-utils`, desktop `document-reader` (OCR), `redline` (annotate and review passages), `draft-cards`, `jot` | The core of the product for tendering. These are the building blocks; section 5 says what is still missing. |
| Microsoft / calendar / deadlines | `microsoft365` (pre-release: Outlook, SharePoint, OneDrive, Calendar, Teams), bundled `teams_pipeline`, `public-holidays` (workday counts for submission deadlines), `event-read` (CalDAV) | Tender teams live in Outlook/SharePoint/Teams; deadlines are workday arithmetic. |
| Messaging bots | bundled platform adapters the company uses (Telegram, WhatsApp, Teams, Slack, Discord, email), `hermes-telegram-business`, `kanban-stream` | **Decided** ("bots"). Which adapters is a per-company choice on top of the pack. |
| Cost and audit | `tokenwatch` (threshold alerts and freeze), `hermes-telemetry` (tokens, cost, budget guardrails), `approval-ledger`, `edit-ledger`, `error-ledger`, `fleet-policy` (records what pinned managed settings each role got) | Shared keys mean shared spend (D15); tender work needs an audit trail of what the agent changed. |
| Safety | `skill-integrity` (rug-pull detection for installed skills), `vaultknox` (secret-leak scan in and out), `scamgate`, `custom-dangerous-patterns` | Cheap guards that fit the access model. |
| Desktop views | `boardstate` (live board: notes, tables, KPI cards), `hermes-rich-ui` (evidence-backed answer cards: tables, charts, timelines), `echarts-in-chat`, `needs-you` (pending approvals), `kanban-gantt` / `kanban-graph` / `hermes-worker-monitor`, `hermes-ledgerline` | Business views; all desktop halves (see section 4). |

### Maybe (your call, per company)
`privacy-gateway` or `hermes-pii` (Presidio PII pseudonymization, adds latency and setup; worth it if tenders carry personal data),
`advisor` / `hermes-verifier` (a second model reviews every turn: better quality, one extra completion per turn), `twenty-crm`,
`s3` (backup), `local_knowledge`, `gurusup-brain`, `machine-library` (standards, patents, papers search), `openalex`, `langfuse`
and `hermes_otel` (observability), `hermes-plugin-guard` (static review of candidate plugins: use it on the admin's side when
adding entries), language packs (`hermes-lang-*`) per customer language.

### Never (categories; hidden from packs, still in the universe for an admin who insists)
- Crypto and wallets: `aave-mcp`, `metamask-wallet`, `agentwalletapi`, `laso-finance`, `pay`, `pay-cli`, `sigrex`, `stalkchain`,
  `greyridge-token-safety`, `hermes-gensyn-*`, `vet402`, `hermes-mpp`.
- Jailbreaks and offensive tooling: `hermes-jailbreak-kernel`, `darkmoon`, `rea`, `shodan`, `hermes-security-audit` (pentest-like,
  use deliberately, not by default).
- Games, themes and novelty: `agent-hold-em`, `minecraft-platform`, `hermes-pokemon`, `pixel-worlds`, `hermes-asgard`,
  `hermes-kemet`, `hermes-pantheon`, `machine-spirit`, `anatomy-museum`, `hermes-boot-screen`, and the other decorative desktop
  entries.
- Personal life and hardware: health (`healthcare`, `health-*`, `eatwise`), `good-student`, `reading-buddy`, smart home
  (`govee`, `switchbot-control`), music (`spotify*`, `tidal`, `hermes-plex-music`), 3D printing and CAD (`klipper-print-watch`,
  `print-job-watch`, `orcaslicer`, `blender`, `davinci-resolve`, `touchdesigner`), travel and shopping (`letsfg`,
  `maqami-travel`, `pricewin`, `kiwi`, `channel3`, `parcel-deliveries`), `wechat-*`, `colorado-traffic`, the `jp-*` family.
- Alternative terminal backends: `bubblewrap`, `singularity`, `cua-spaces`, `cloudflare-sandbox`, `coder`, `hermes-wslc-sandbox`,
  `deepinfra-sandbox`: our sandbox is the company's Docker-in-Docker daemon (D12); a second isolation story is a way around it.
- Computer and phone control: `computer_use` providers, `phone-harness`, `android-emulator`, `hermes-windows-computer-use`.
- Exposing or importing the agent: `mcp-serve`, `agent-importers`, `myswarm`, `mybot-farm`.

## 4. Plugins with a backend and a desktop view, remote-first

What exists today (read from `apps/desktop/electron/desktop-plugins-root.ts` and `apps/desktop/src/contrib/runtime-loader.ts`):
- A desktop half loads **only from the local machine's** `<hermes home>/desktop-plugins/` or the local
  `plugins/<name>/desktop/plugin.js`. In a remote-first install the user's machine has no agent plugins, so nothing delivers a
  company's desktop half to it.
- A loaded desktop plugin runs as ESM in the renderer with **full app authority** (gateway RPC, REST, storage). The loader's own
  header says error isolation only, and that a remote source "needs a real boundary (iframe/worker + CSP + capability gating)
  before it can land".
- The web dashboard already serves plugin assets from the gateway (`/dashboard-plugins/<name>/...`).

Recommendation, three tiers:
1. **Gateway-served views in a sandboxed iframe (default for Sentio's own plugins, for example the document viewer).** The
   backend half serves the page; the app or browser renders it in an iframe with a strict CSP and talks to the agent through a
   small capability-gated message bridge (read session, read artifact, request approval: no raw RPC). Works identically in the
   desktop app and the browser dashboard, needs no per-laptop install, and a compromised plugin cannot reach the renderer's
   authority.
2. **Desktop halves that need native app authority** (status-bar chips, themes, palette commands) are either **bundled into the
   Light installer by the pack** (catalog pins the SHA, the build includes it) or delivered as a **signed bundle**: the app checks
   an ed25519 signature from a Sentio key that is baked into the app, and the pin comes from the app, never from the gateway. A
   gateway that is merely compromised must not be able to push code into every employee's renderer.
3. **Local desktop plugins** (today's behaviour) stay for personal and development use and are **off on company-managed
   connections**.

Open questions: the exact capability list for the iframe bridge; whether the pack's bundled desktop halves force one installer per
pack or a small set of installers (see FORK_DECISIONS O9, one remote-first installer).

## 5. Document operations (the product's centre of gravity)

Building blocks exist (section 3, Documents). What is still missing for tendering, and what I would build as one Sentio plugin
(backend plus a tier-1 viewer):
- A proper document viewer: PDF and Office render with page thumbnails, search, and click-through from an agent claim to the
  exact page and passage it came from.
- Requirement extraction: a tender's mandatory and scored requirements into a matrix with source page references, then a
  compliance checklist the team and agent both fill.
- Clause and version comparison across tender addenda, with a redline view.
- Fill-in of forms and schedules with review before export, and export to the formats the issuer demands.
Start by running the existing blocks (`doc-markdown`, `pdf-surgeon`, `redline`, `document-reader`, `artifacts`) against real tender
documents to see where they break, then specify the plugin from that.

## 6. Risks to decide on

- **R1. TinyFish as default sends search queries to a third party.** Fine for free search; a tender query reveals what a company
  is bidding on. Make it a per-pack default that a privacy-sensitive company can switch off, and say so in the customer terms.
- **R2. Memory must be per company.** One self-hosted Hindsight shared across customers would mix their data. A company gateway
  needs its own bank (at minimum) or its own instance; this is part of the planned memory fork.
- **R3. Some entries are pre-release or lightly maintained** (`microsoft365`, many community plugins). The pack pins exact SHAs;
  a SHA bump is a reviewed change here.
