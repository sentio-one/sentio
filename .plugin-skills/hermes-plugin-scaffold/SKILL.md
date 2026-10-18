---
name: hermes-plugin-scaffold
description: "Hermes plugin layout, plugin.yaml manifest reference (v1 and v2), discovery sources and precedence, plugin kinds, enabling/opt-in states, capabilities, config_schema, requires_env, locales, and register(ctx) load semantics. Use when creating or fixing a Hermes plugin's structure or manifest."
license: MIT
---

# Hermes Plugin Scaffold

> Verified against source at commit `e5eb6b36` (2026-10-15). Static reading only. Source of
> truth: `hermes_cli/plugins_manifest.py`, `hermes_cli/plugins.py`, `hermes_cli/config.py`.

## Layout

```
~/.hermes/plugins/<name>/
├── plugin.yaml        # manifest (required for a native plugin)
├── __init__.py        # register(ctx) entry point (required unless manifest-only)
├── schemas.py         # optional: tool schemas
├── tools.py           # optional: tool handlers
├── pyproject.toml     # optional: preferred place for Python dependencies
├── dashboard/         # optional: web-dashboard half (manifest.json + plugin_api.py)
├── desktop/           # optional: desktop-app half (plugin.js)
└── skills/            # optional: skills registered by ctx.register_skill()
```

Discovery scans **one level** — a plugin dir directly under the root, or one category level
(`plugins/platforms/<name>/`, `plugins/model-providers/<name>/`, …). Deeper nesting is ignored.

## Discovery sources and precedence

| Source | Path | Notes |
|---|---|---|
| Bundled | `<repo>/plugins/` | ships with Hermes |
| User | `~/.hermes/plugins/` | personal; **later wins on name collision** |
| Project | `./.hermes/plugins/` | needs `HERMES_ENABLE_PROJECT_PLUGINS=true` |
| pip | `hermes_agent.plugins` entry points | distributed packages |
| Nix | `services.hermes-agent.extraPlugins` | symlinked with a `nix-managed-` prefix |

Later sources override earlier ones on a name collision, so a user plugin replaces a bundled one of
the same name — **except memory providers**, which resolve the other way (bundled wins).

Category discovery routes to specialised loaders:

| Sub-directory | Kind | Discovery |
|---|---|---|
| `plugins/` root | `standalone` / `backend` | `PluginManager` |
| `plugins/platforms/<name>/` | `platform` | `PluginManager` (one level deeper) |
| `plugins/image_gen/<name>/` | `backend` | `PluginManager` |
| `plugins/memory/<name>/` | `exclusive` | own loader in `plugins/memory/__init__.py` |
| `plugins/context_engine/<name>/` | `exclusive` | own loader |
| `plugins/model-providers/<name>/` | `model-provider` | `providers/__init__.py`, lazy |

Valid `kind:` values (`plugins_manifest.py:_VALID_PLUGIN_KINDS`):
`standalone`, `backend`, `exclusive`, `platform`, `model-provider`.

An omitted `kind` is **auto-detected from `__init__.py`** for memory / cron / computer-use
providers (`register_memory_provider`, `MemoryProvider`, `register_cron_scheduler`,
`CronScheduler`, `register_computer_use_provider`, `ComputerUseProvider` → `exclusive`) and for
model providers (`register_provider` + `ProviderProfile` → `model-provider`).

## plugin.yaml — field reference

The parser's known-field list (`plugins_manifest.py:_KNOWN_MANIFEST_FIELDS`) is the authority.
Unknown fields are **ignored with a warning** (debug for v1), never a load failure — so a newer
field on an older Hermes is safe.

```yaml
name: my-plugin              # the registry name; defaults to the directory name
version: "1.0.0"
description: What it does
author: Your Name
kind: standalone             # see above
label: My Plugin             # display label (platform plugins)

provides_tools:              # every tool register() adds
  - my_tool
provides_hooks:              # every hook register() subscribes to
  - post_tool_call
# `hooks:` is an accepted legacy alias for provides_hooks (source: plugins_manifest.py:551)

requires_env:                # gate loading on credentials; prompted at install
  - MY_API_KEY               # bare string
  - name: OTHER_KEY          # rich form
    description: "Key for Other"
    url: "https://other.com/keys"
    secret: true             # also accepted: password: true (see below)

optional_env:                # surfaced in `hermes config` but never blocks loading
  - name: MY_HOME_CHANNEL
    description: "Default channel for cron delivery"
    password: false

capabilities:                # privileged host surfaces → one consent screen
  - tools.override
  - llm.model_override

requires_hermes: ">=0.21.5"  # SemVer floor; unsatisfied → plugin skipped before import
requires_auth:               # providers whose sign-in the plugin borrows (today: openai-codex)
  - openai-codex

# ── manifest v2 (all optional; absent = v1, supported forever) ──
manifest_version: 2          # file-format version; current max 2
api_version: 1               # runtime API generation — a separate axis from manifest_version
license: MIT
homepage: https://github.com/owner/repo
tags: [gateway, demo]
requires_plugins:            # advisory inter-plugin deps; also orders register() calls
  - id: other-plugin
    version_range: ">=1.0,<2"
python_dependencies:         # declared deps (prefer pyproject.toml); install needs consent
  - "requests>=2.0,<3"
python_runtime: external     # the plugin manages its own interpreter; Hermes installs nothing
config_schema:               # settings form + validation for plugins.entries.<id>.settings
  api_url: {type: str, default: "", description: "Service endpoint"}
  api_key: {type: secret, env: MY_PLUGIN_API_KEY}
provides_locales:            # language pack: locales/<id>[.tui|.desktop].yaml auto-registered
  - id: pl
    endonym: Polski
    rtl: false
emits: []                    # advisory event-bus declarations
listens: []
```

### `requires_env` rich-form keys — the `secret` vs `password` question, settled

Both spellings are honoured. `hermes_cli/config.py:_platform_manifest_env_entries`:

```python
is_secret = bool(meta.get("password") or meta.get("secret"))
if not is_secret and not meta.get("password") is False:
    is_secret = name.upper().endswith(PLATFORM_SECRET_ENV_SUFFIXES)
```

`PLATFORM_SECRET_ENV_SUFFIXES = ("_TOKEN", "_SECRET", "_KEY", "_PASSWORD", "_JSON")`.

So: `secret: true` and `password: true` are equivalent; a name ending in one of those suffixes is
treated as secret even with neither key; `password: false` forces it non-secret. Supported dict
keys: `name` (required), `description`, `prompt`, `url`, `password`/`secret`, `category`
(default `messaging`). `hermes plugins validate` requires every `name` to be `UPPER_SNAKE_CASE`.

### `config_schema`

Types (`_CONFIG_SCHEMA_TYPES`, plus JSON-schema aliases): `str`/`string`, `int`/`integer`,
`float`/`number`, `bool`/`boolean`, `list`/`array`, `dict`/`object`, `secret`.
Beyond `type`/`default`/`description`/`required`, entries accept `label`/`title`, `choices`
(or `enum` — turns a `str` into a dropdown), and for `secret` an `env:` name. Types mismatch →
**warning naming the key and type, never a load failure**. A `secret` value is never written to
`config.yaml` — the Desktop stores it through the `.env` credential route and the plugin reads it
with `get_secret(NAME)`.

### Capabilities

Known ids (`hermes_cli/plugin_capabilities.py`): `tools.override`, `llm.provider_override`,
`llm.model_override`, `llm.agent_id_override`, `llm.profile_override`, `llm.task_override`,
`gateway.platform_actions`. Unknown ids are ignored.

Declaring a capability is **consent metadata, not a grant**: the user consents once at
install/enable, and the grant is recorded under `plugins.entries.<id>.granted_capabilities`. An
unconsented capability is simply **off (fail closed)** — probe and degrade:

```python
def register(ctx):
    if ctx.has_capability("tools.override"):
        ctx.register_tool(..., name="browser_navigate", override=True)
    else:
        ctx.register_tool(..., name="my_browser_navigate")  # non-conflicting name
```

Capabilities are an audit/consent layer, **not a sandbox** — plugins run in-process as ordinary
Python.

## `register(ctx)` load semantics

- Called **once per plugin at startup** (and again on a forced mid-run reload).
- `ctx` is a `PluginContext` bound to your plugin's identity; it tracks every registration so
  unload restores the previous state.
- A crash inside `register()` disables **only that plugin**; Hermes continues and the reason shows
  in `hermes plugins list`.
- A plugin that overruns `plugins.load_timeout_seconds` (default 10; 0 disables; clamped ≤600) is
  skipped with `load timed out after Ns`; the stuck thread is abandoned.
- Hook callbacks that overrun `plugins.hook_callback_timeout` (default 30; 0 disables; ≤600) are
  cut off — a timed-out `pre_tool_call` **fails closed** (blocks the tool).
- Import order honours `requires_plugins` (topological; alphabetical tiebreak; cycles warn and
  fall back to alphabetical).

## Enabling — plugins are opt-in

Discovery finds a plugin (it shows in `hermes plugins` and `/plugins`) but **nothing loads** until
it is allow-listed. Bundled platform/backend/model-provider/memory/context-engine plugins are
exempt and auto-load — selection there happens through `<category>.provider` / `.enabled` config.

```yaml
plugins:
  enabled: [my-plugin]
  disabled: [noisy-plugin]      # always wins if a name is in both
  clone_timeout_seconds: 300
  hook_callback_timeout: 30
  load_timeout_seconds: 10
```

Three states: `enabled`, `disabled` (explicit off, wins over enabled), `not enabled`
(discovered, never opted in — the default).

```bash
hermes plugins                 # interactive toggle
hermes plugins list            # all three states
hermes plugins enable <name>
hermes plugins disable <name>
```

Disabling a plugin also removes its toolset from saved `platform_toolsets` lists and re-adds it on
enable; with no saved selection the lists are untouched (a plugin toolset is on by default).

## Debugging discovery

```bash
HERMES_PLUGINS_DEBUG=1 hermes plugins list
```

Prints scanned directories, per-manifest key/name/kind/source/path, skip reasons (`disabled via
config`, `not enabled in config`, `no plugin.yaml, depth cap reached`), what `register()` added,
and a full traceback on parse or registration failure. The same logs always land in
`~/.hermes/logs/agent.log` (failures at WARNING).

## Common "plugin doesn't appear" causes

- **Not enabled** — run `hermes plugins enable <name>` (the name can be `<category>/<plugin>`).
- **Too deep** — only one category level is scanned.
- **No `__init__.py`** — a native plugin needs both `plugin.yaml` and `register(ctx)`.
- **Wrong `kind`** — a gateway adapter needs `kind: platform`; memory providers are auto-detected
  as `exclusive` and activated via `memory.provider`, not `plugins.enabled`.
