# Hermes Plugin Builder Skills

A set of agent skills for building **Hermes Agent plugins** — the agent-side Python plugin system
(`plugin.yaml` + `register(ctx)`) that adds tools, hooks, commands, gateway platforms and provider
backends without touching Hermes core.

Every API name, signature, manifest field, config key, path and code template in these skills was
read from the **actual source**, not from a web page.

## Verification stamp

> **Verified against:** `NousResearch/hermes-agent` at commit `e5eb6b36` (2026-10-15), shallow clone
> of `main`.
> **Method:** static source reading only. No Hermes code was executed, no dependencies installed.
> The templates are syntax-checked, not run.
> **Re-check before relying on details** if you are on a different release.

The canonical, always-current sources live in the repo. Where this set and the repo disagree, the
repo wins:

| Topic | Read |
|---|---|
| Authoring guide | `website/docs/developer-guide/plugins/index.md` |
| Discovery, enabling, capabilities | `website/docs/user-guide/features/plugins.md` |
| Hook catalogue | `website/docs/user-guide/features/hooks.md` |
| `PluginContext` — every signature, `VALID_HOOKS` | `hermes_cli/plugins.py` |
| Manifest parser + known fields | `hermes_cli/plugins_manifest.py` |
| Catalog admission rules | `plugin-catalog/README.md` |
| Runtime validation (`doctor`) | `hermes_cli/plugin_dev.py` |
| ~100 real plugins | `plugins/` |

## The skills

| Skill | Covers |
|---|---|
| **hermes-plugin-builder** | Entry point: scope, the footprint ladder, decision tree, 2-file quickstart |
| **hermes-plugin-scaffold** | Layout, discovery + precedence, `plugin.yaml` v1/v2 reference, kinds, enabling, capabilities |
| **hermes-plugin-tools** | `register_tool`, handler contract, schemas, `check_fn`, async, overriding built-ins |
| **hermes-plugin-hooks** | `register_hook`, the full `VALID_HOOKS` set, return contracts, middleware, shell/gateway hooks |
| **hermes-plugin-commands-skills** | Slash commands, CLI subcommands, bundled skills, blueprints, injection, event bus |
| **hermes-plugin-platform-adapters** | `BasePlatformAdapter`, `register_platform`, env/YAML bridges, cron delivery, native handlers |
| **hermes-plugin-ui-extensions** | Desktop SDK, TUI widgets, web dashboard plugins, the shared backend namespace |
| **hermes-plugin-other-providers** | Memory / context-engine / model / image / video / web / browser / terminal / computer-use / secret / TTS providers, `ctx.llm` |
| **hermes-plugin-dev-workflow** | `doctor`, `validate`, deps + consent, security scanning, host isolation, storage, testing |
| **hermes-plugin-catalog-publishing** | Entry schema, the 17 admission rules, PR/CI, removal, packs, install links |

## Not covered (deliberately)

These are **not** plugin authoring, and Hermes' own bundled `hermes-agent` skill already covers the
first three:

- Desktop app UI plugins (`@hermes/plugin-sdk`) — summarised in `hermes-plugin-ui-extensions`, deep
  reference in `skills/autonomous-ai-agents/hermes-agent/references/desktop-plugins.md`.
- TUI widgets — same split.
- Web dashboard themes and plugins.
- MCP servers, shell hooks, gateway event hooks — config-driven, not Python plugins.
- **Core** contribution (adding a tool to `tools/` and `toolsets.py`) — that is a PR to
  `hermes-agent`, not a plugin. See `website/docs/developer-guide/adding-tools.md`.

## Open questions from the previous pass — resolved

An earlier attempt at this skill set was written by reading the docs **through a web summariser**,
and it left a list of items it could not confirm. Reading the source settled all of them:

| Was unresolved | Answer (source) |
|---|---|
| `VALID_HOOKS` count — docs said 27, a code mirror said 6 | **44.** `hermes_cli/plugins.py:107`. The docs page is stale. |
| `requires_env`: `secret` or `password`? | **Both.** `hermes_cli/config.py:3985` — `is_secret = bool(meta.get("password") or meta.get("secret"))`, plus a `_TOKEN`/`_SECRET`/`_KEY`/`_PASSWORD`/`_JSON` suffix fallback and `password: false` opt-out. |
| Does `complete_structured` raise or return nothing on a schema violation? | **It raises `ValueError`.** `agent/plugin_llm.py:333`. Non-JSON or unparseable text returns `parsed=None, content_type="text"` with **no** exception; a missing `jsonschema` package skips validation. |
| Do tool schemas need a `name` key? | **No.** `tools/registry.py:900` builds `{**entry.schema, "name": entry.name}` — the `name=` argument overwrites any `name` in the schema. |
| Is the `hooks:` list in `plugin.yaml` a requirement? | **No — it is an accepted legacy alias** for `provides_hooks`. `hermes_cli/plugins_manifest.py:551`. |
| Hook callbacks and `**kwargs` | **Required.** `hermes plugins doctor` errors on any hook callback that does not accept `**kwargs` (`hermes_cli/plugin_dev.py:408`). |
| `register_hook` signature / valid hooks | `register_hook(hook_name: str, callback: Callable) -> PluginRegistration`; valid names are exactly the 44 in `VALID_HOOKS`. |
| Built-in tool renames (`todo_list`, `cronjob_manage`, `process_manage`) | **Real.** `evals/core_tool_deferral/worker.py:282` — `LEGACY = {"todo": "todo_list", "cronjob": "cronjob_manage", "process": "process_manage", ...}`. |
| Catalog rules | Verified verbatim from `plugin-catalog/README.md` (mirrored into the docs page; a test fails the build if they drift). |
| Providers moved out of core | Verified from `plugins/AGENTS.md` — memory providers (Honcho, Mem0, Supermemory, …), Home Assistant and Spotify left core for the catalog. |

**Still not executable-verified** (static reading only): the templates have not been run against a
live install. Run `hermes plugins doctor . --ci` and
`hermes plugins validate /path/to/plugin --install-deps` against a real Hermes to close that gap —
they are the same checks catalogue CI runs.

## Installing

Each folder is a self-contained skill (`SKILL.md` with `name` + `description` frontmatter).

**For Claude Code** (the original intent — these help an agent write Hermes plugins):

```bash
cp -r hermes-plugin-* ~/.claude/skills/
```

**For Hermes** as a skills tap — any repo of `skills/<name>/SKILL.md` directories works:

```bash
hermes skills tap add <owner>/<repo>          # after pushing this folder as a repo
hermes skills search hermes-plugin
hermes skills install <repo>/hermes-plugin-builder
```

Or drop them straight into the home:

```bash
cp -r hermes-plugin-* ~/.hermes/skills/
```

## Layout

```
hermes-plugin-builder/SKILL.md
hermes-plugin-scaffold/SKILL.md
hermes-plugin-tools/SKILL.md
hermes-plugin-hooks/SKILL.md
hermes-plugin-commands-skills/SKILL.md
hermes-plugin-platform-adapters/SKILL.md
hermes-plugin-ui-extensions/SKILL.md
hermes-plugin-other-providers/SKILL.md
hermes-plugin-dev-workflow/SKILL.md
hermes-plugin-catalog-publishing/SKILL.md
README.md
```
