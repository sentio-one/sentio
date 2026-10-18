---
name: hermes-plugin-catalog-publishing
description: "Publish a Hermes plugin to the plugin catalog — entry schema, the 17 admission rules, the PR/CI process, validate checks, delisting vs removal, plugin packs and install links. Use when the user wants their plugin listed in Hermes' catalog or distributed to others."
license: MIT
---

# Hermes Plugin Catalog Publishing

> Verified against source at commit `e5eb6b36` (2026-10-15). Static reading only.
> Sources: `plugin-catalog/README.md` (canonical admission rules),
> `website/docs/developer-guide/plugins/catalog-submission.md` (mirrors them word for word;
> `tests/website/test_catalog_rules_mirror.py` fails the build if they drift),
> `website/docs/user-guide/features/plugin-catalog.md`.

## How the catalog works

The catalog **is** the trust signal: presence means a human reviewed the metadata and declared
capabilities at an exact pinned commit. There is no self-serve registry and no automated ingestion.
Users install by bare name; the name resolves to the entry's repo at its pinned SHA.

```bash
hermes plugins search <term>
hermes plugins browse
hermes plugins info <name>
hermes plugins install <catalog-name>          # reviewed pin
```

Explicit `owner/repo` or Git-URL identifiers **never** touch the catalog and are flagged as custom
(unreviewed) sources. `removed.yaml` is the blocklist: every install path (CLI, dashboard, TUI)
refuses a match, and only the CLI has a loud `--allow-removed` escape hatch, recorded on the install
record.

**Cataloged ≠ audited.** Installing still goes through the normal consent flow; enabling is a
separate explicit step, and tool-override rights need their own grant.

## Before you submit

- A **public** repository (`https://`, GitHub or GitLab).
- A **loadable plugin at the pinned commit**: `plugin.yaml` plus at least one entrypoint Hermes
  loads — `__init__.py` (Python), `desktop/plugin.js` (Desktop), `plugin.json` (portable Agent
  Plugin), or `dashboard/manifest.json`. In a monorepo, point `subdir` at the directory.
- **Public surfaces only** — hooks, middleware, `ctx.register_*`, provider plugins, the Desktop
  plugin SDK. Never patch Hermes code or Desktop markup at runtime.
- **Validate locally at the exact commit you are about to pin:**

  ```bash
  hermes plugins validate /path/to/your-plugin --install-deps
  ```

  Fix every failure and **read the warnings** — a reviewer will.

## Entry schema

One YAML file per entry, `plugin-catalog/<name>.yaml`:

```yaml
name: example-plugin        # [a-z0-9_-]{1,64} — the catalog key
repo: https://github.com/owner/repo   # https:// only
sha: <40-hex commit sha>    # MANDATORY exact pin
subdir: ""                  # optional; plain relative path ([A-Za-z0-9._/-]+; no '..', '.', absolute, backslash)
description: One-line description.
maintainer: OwnerName
tier: official              # official | community (default community)
category: memory            # desktop | memory | platform | web | tools | voice | automation | models | general (default desktop)
requires_hermes: ">=0.19"   # optional SemVer floor
docs_url: ""                # optional
version: "1.4.0"            # optional human label for the sha (quote it); shown as "1.4.0 @ abcd1234"
image: ""                   # optional https image on a GitHub host, 2:1
screenshots: []             # optional, up to 6 https images on a GitHub host
readme: true                # optional, default true; README at the PINNED sha renders on /docs/plugins/<name>
platforms: []               # optional, e.g. [linux, macos]; empty = all
title: ""                   # optional human name on cards
onboarding: false           # optional; Desktop onboarding card (official entries only)
known_issues: []            # optional strings shown at the install prompt; informational, never blocks
capabilities:
  provides_tools: []
  provides_hooks: []
  provides_middleware: []
  requires_env: []
```

`version`, `image`, `screenshots` and `readme` are cosmetic — none is parsed or used to pick what
installs; **the `sha` is the release**. Bump `version` in the same PR that bumps `sha`. Images must
live on `raw.githubusercontent.com`, `github.com` or `*.githubusercontent.com`, and should be pinned
to the entry's commit. Every entry gets a page at
`https://hermes-agent.nousresearch.com/docs/plugins/<name>` generated at docs build time; the README
is fetched from the pinned sha, so the page shows exactly what the reviewer read.

## The admission rules (canonical summary)

1. **Human-merged gate** — entries added only via a PR reviewed by a maintainer.
2. **Exact SHA pins mandatory** — branches, tags and short SHAs are rejected by the loader.
3. **No self-updating code** — no in-app "check for updates", signed-release downloaders, or remote
   `plugin.js` loaders. The SHA pin *is* the trust model. Keep an updater in your standalone
   distribution; strip it from the catalog build.
4. **SHA bumps are new PRs** — the old→new diff is re-reviewed.
5. **Owner-or-major-contributor submissions**, or a maintainer-curated sweep. Drive-by submissions of
   third-party repos are declined. Swept-in authors keep control.
6. **Declared capabilities must match reality** — tools, hooks, middleware and env vars must match
   what registers at the pinned commit; validation fails the entry otherwise.
7. **The install scanner runs at admission** — `dangerous` fails; `caution` is a warning the reviewer
   reads before merging. Installs at the pinned SHA then accept `caution` without a prompt.
8. **Desktop plugins stay inside the SDK surface** — no prototype patching
   (`X.prototype.y =`, `Object.defineProperty(...prototype`), no `eval`/`new Function`, no `import()`
   of anything but `@hermes/plugin-sdk` / `react`, no script-tag injection, no reaching into the
   app's internal stores or markup (`data-slot`/`data-tour`/`data-sidebar`/`data-testid` queries, a
   `document.body` MutationObserver). `hermes plugins validate` refuses these (`desktop surface`).
   Need an SDK capability? Ask for an SDK hook instead of patching around it.
9. **No runtime overrides of Hermes core** — no replacing/wrapping/rebinding core functions,
   methods, module attributes or private dicts (`AIAgent.<method> = ...`, `setattr(server, ...)`,
   `sys.modules[...]`). Refused at admission (`no core override`); each import of an undocumented
   core module warns by file and line. Missing a hook? Open an issue — the project would rather add
   the seam than list a patch.
10. **Dependency security policy is the plugin's** — Hermes' 3-day `exclude-newer` gate covers only
    Hermes' own deps. Reviewers read your list at the pinned SHA and request the **oldest
    API-compatible floor plus an upper bound** for bare floors. Run your own `uv --exclude-newer` in
    CI and pin your lock.
11. **Credentials come through Hermes, never from files** — declared `requires_env` /
    `config_schema` secrets are read with `agent.secret_scope.get_secret(NAME)`. A subscription
    sign-in is used only through `credentialed_provider_request` with `requires_auth`. Never read
    `.env`, `auth.json`, another tool's login or any credential store directly, and never refresh,
    rotate or write OAuth tokens.
12. **Approvals and unattended runs are respected** — no auto-approving, no disabling guards, no
    spawning Hermes/shell children that inherit YOLO or non-interactive mode. Anything that waits for
    a person must fail cleanly or time out under cron and the gateway instead of hanging the agent.
13. **Risky behaviour is disclosed** — third-party network calls, reads outside your own data, shell
    commands, long-running background processes, stored credentials go in the PR description and the
    README. Telemetry is opt-in.
14. **Compatibility metadata is truthful** — `requires_hermes` is a **SemVer** floor (`">=0.21.5"`),
    never a CalVer date, never newer than the current release; `version` matches the pinned code;
    deps resolve under Hermes' core constraints.
15. **No skins or forks of bundled plugins** — a change to a bundled plugin is a PR against
    `hermes-agent`, not a competing listing.
16. **One listing per plugin lineage** — a fork is listed only when materially different (different
    transport/architecture, or capability the original lacks and its author declined or ignored for
    30 days). A listed fork names its origin in its disclosure line.
17. **No unscannable executable payloads** — no compiled binaries (detected by file header: ELF,
    Mach-O, PE/`MZ`, WebAssembly), no committed `.pyc`/`.pyo`, no archives hiding non-source members.
    A native component must be built by the plugin repo's own public GitHub Actions from source and
    carry an `actions/attest-build-provenance` attestation, verified with
    `gh attestation verify --repo <owner/repo> --source-digest <pinned sha>`. A binary the user's
    machine fetches at setup is allowed only from the upstream official source, checksum-pinned and
    disclosed — never bundled, never from an unofficial mirror.

## Opening the PR

1. Add **one** file, `plugin-catalog/<name>.yaml`, to
   [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent).
2. In the description: what it does, which Hermes surfaces it uses, and everything rule 13 asks you
   to disclose. Add screenshots for anything with a UI.
3. Wait for the catalog CI job green — it clones your repo at the pinned commit and runs
   `hermes plugins validate`.
4. A maintainer reviews the pinned tree and merges, asks for changes, or declines. **Rules 3 and 9
   problems are declines, not bug-fix requests** — the plugin has to change its design before it can
   be listed.

## Names

The catalog key and the manifest `name:` are what users search, install, and — for memory providers
— put in `memory.provider`. A `memory`/`exclusive` entry must not reuse another provider's name or a
well-known upstream project's name it is not affiliated with: two providers registering the same
`register_memory_provider` name make `memory.provider` ambiguous. Reviewers check the **registered**
provider name, not just the file name.

## Updating, delisting, removal

- **Update** = a new PR bumping `sha` (and `version`). Installed plugins compare their recorded sed
  sha against the live pin: `hermes plugins list --json` reports `update_available`, the Desktop
  Plugins tab shows an **Update** button, and `hermes plugins update <name>` checks out the new pin.
  A re-pin that adds tools, hooks, Python deps, capabilities, `requires_auth` or a Desktop UI half
  shows the delta and asks before moving.
- **Delisting** deletes the entry's file (unmaintained, superseded, name-squatting, or no longer
  meeting a rule). Users who already installed it keep it, and it is welcome back once fixed.
  Reviewers give affected authors an issue and time to update before anything is removed.
- **Removal** adds the plugin to `removed.yaml` — reserved for security and policy incidents.
  Installers refuse it, and installed copies stop updating and cannot be enabled.

## Distributing without the catalog

**Plugin packs** — a shareable `hermes-pack.yaml` that pins a set of plugins:

```yaml
name: voice-assistant-pack
description: STT + streaming TTS + approval relay
plugins:
  - name: hermes-telegram-business              # catalog name…
    ref: e905f3bc5eeaa5a9dab9bc5155601b3ebec75757
  - repo: owner/approval-relay                  # …or owner/repo
    ref: 8f3c2d1a9b4e5f6071829304a5b6c7d8e9f00112
    subdir: plugins/relay
```

```bash
hermes plugins pack show ./hermes-pack.yaml      # dry-run review
hermes plugins pack install ./hermes-pack.yaml
hermes plugins pack export --enabled-only > hermes-pack.yaml
```

Every `ref` must be an exact 40-char SHA. `pack install` shows a mandatory review screen then one
confirmation; **capabilities are still consented per plugin**. There is no `--yes`, and
non-interactive sessions cannot install packs. Secrets never travel in packs — `config:` seeds are
non-secret `plugins.entries.<id>` keys only (secret-shaped names, capability grants and legacy
`allow_*` gates are rejected on install and stripped on export).

**Install links** — the Desktop registers the `hermes://` scheme:

```html
<a href="hermes://plugin/install?repo=owner/repo&enable=1">Install in Hermes</a>
hermes://plugin/install?catalog=NAME          <!-- reviewed catalog entry at its pin -->
hermes://plugin/install?repo=owner/repo&force=1
```

Deep links **never auto-install**: a `repo=` link opens a confirmation dialog with the repo id, a
"Before you install" note and GitHub browse/clone links; a `catalog=` link opens the reviewed entry
and installs at the pinned commit, never the branch tip. A name that is not in the catalog shows an
error toast and is never reinterpreted as a git path. Dev builds use `hermes-dev://`.

**pip** — declare an entry point for a distributed package:

```toml
[project.entry-points."hermes_agent.plugins"]
my-plugin = "my_plugin_package"
```

A pip-distributed plugin has no `plugin.yaml` directory once installed, so declare capabilities in
distribution metadata via the companion `hermes_agent.plugin_capabilities` entry-point group, naming
each declaration `<plugin-id>.<capability-id>` and pointing it at the same object as your
`hermes_agent.plugins` entry point — Hermes reads these without importing your code.

**Dashboard plugin distribution:** dashboard plugins are installed by directory layout, not pip. The
cleanest path today is a Git repo the user clones into `~/.hermes/plugins/`; a pip-based installer
for dashboard plugins is not wired up.
