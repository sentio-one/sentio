---
name: hermes-plugin-builder
description: "Build Hermes Agent plugins (agent-side Python: tools, hooks, commands, platform adapters, provider backends, catalog submission). Use when the user wants a custom tool, hook, slash command, gateway platform, model/memory/image provider, or wants to publish a plugin to the Hermes catalog."
license: MIT
---

# Hermes Plugin Builder

Entry point for writing **Hermes Agent plugins** — the agent-side Python plugin system
(`plugin.yaml` + `register(ctx)`) that adds tools, hooks, commands, gateway platforms and
provider backends without touching Hermes core.

> Verified against Hermes Agent source at commit `e5eb6b36` (2026-10-15), a shallow clone of
> `NousResearch/hermes-agent` main. Read statically — no Hermes code was executed. Re-check
> against the source if you are on a different release.

## Scope: what this skill does NOT cover

Hermes has several unrelated plugin/extension systems that share the word "plugin". This skill
set covers only the **agent-side Python** one. For the others use Hermes' own bundled
`hermes-agent` skill (`skills/autonomous-ai-agents/hermes-agent/`):

| Surface | SDK / marker | Where | Use |
|---|---|---|---|
| **Agent plugin** (this skill) | `plugin.yaml` + `register(ctx)` | `~/.hermes/plugins/<name>/` | Tools, hooks, commands, providers |
| Desktop app UI | `@hermes/plugin-sdk` | `~/.hermes/desktop-plugins/<id>/plugin.js` | Panes, statusbar, themes — see `hermes-plugin-ui-extensions` |
| Ink TUI widgets | `defineWidgetApp` | `~/.hermes/tui-widgets/<name>.mjs` | Docked terminal panels — see `hermes-plugin-ui-extensions` |
| Web dashboard | `window.__HERMES_PLUGIN_SDK__` + `dashboard/manifest.json` | `~/.hermes/plugins/<id>/dashboard/` | Dashboard tabs/slots — see `hermes-plugin-ui-extensions` |
| MCP server tools | `mcp_servers:` in config | `config.yaml` | External tools, no Python needed |
| Shell hooks | `hooks:` list | `config.yaml` | Run a shell command on an event |
| Gateway event hooks | `HOOK.yaml` + `handler.py` | `~/.hermes/hooks/<name>/` | Gateway lifecycle events |

## Choose the highest rung that solves it

The Hermes rubric is a "footprint ladder". Do not write a plugin when a lower rung works
(source: root `AGENTS.md`):

1. **Extend existing code** — the capability may already exist as a `ctx` method, a config key,
   or a bundled plugin you can enable.
2. **CLI command + skill** — a procedure an agent follows, not code the model calls.
3. **Service-gated tool** — a tool that only appears when a probe passes (`check_fn`).
4. **Plugin** — the default answer for custom tools/hooks/providers. ← this skill set
5. **MCP server** — someone else's tool surface; declare it in config.
6. **New core tool** — only for something fundamental and broadly useful; needs a PR to
   `NousResearch/hermes-agent` and edits to `tools/` + `toolsets.py`.

A plugin must **never** patch Hermes internals (no `AIAgent.method = ...`, `setattr` on a Hermes
module, `sys.modules[...]` writes). The catalog refuses it at admission. If the hook or `ctx`
method you need does not exist, open an issue asking for the seam.

## Decision tree — which skill to load

| The user wants… | Load |
|---|---|
| A tool the model can call | `hermes-plugin-tools` |
| To run code on an event (block a tool, inject context, log, transform output) | `hermes-plugin-hooks` |
| A `/slash` command, a `hermes <name>` subcommand, or a skill shipped by the plugin | `hermes-plugin-commands-skills` |
| A gateway channel (Discord/Telegram/IRC-like), platform buttons/reactions | `hermes-plugin-platform-adapters` |
| A desktop pane / TUI widget / dashboard tab | `hermes-plugin-ui-extensions` |
| A memory, context-engine, model, image/video, web-search, browser, terminal, computer-use, secret-source or TTS/STT backend; host-owned LLM calls | `hermes-plugin-other-providers` |
| Layout, manifest fields, discovery, enabling, capabilities, settings | `hermes-plugin-scaffold` |
| To test, debug, validate, package deps, or run isolated | `hermes-plugin-dev-workflow` |
| To publish it in the Hermes plugin catalog | `hermes-plugin-catalog-publishing` |
| Anything not a plugin at all | Hermes' bundled `hermes-agent` skill |

## Quickstart — a working tool plugin in two files

`~/.hermes/plugins/hello/plugin.yaml`

```yaml
name: hello
version: 1.0.0
description: Greets a name
provides_tools:
  - hello_world
```

`~/.hermes/plugins/hello/__init__.py`

```python
import json


def register(ctx):
    def handler(args, **kwargs):
        name = args.get("name", "World")
        return json.dumps({"success": True, "greeting": f"Hello, {name}!"})

    ctx.register_tool(
        name="hello_world",
        toolset="hello_world",
        schema={
            "name": "hello_world",
            "description": "Return a friendly greeting for the given name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Name to greet"}},
                "required": ["name"],
            },
        },
        handler=handler,
    )
```

Then enable it — **plugins are opt-in**; discovery lists it but nothing loads until it is in the
allow-list:

```bash
hermes plugins enable hello        # or add to plugins.enabled in ~/.hermes/config.yaml
```

`register(ctx)` runs once at startup. A crash inside it disables only that plugin.

## Hard rules (violating these is the usual cause of a dead plugin)

- **Handlers return a JSON string**, always — including on error. Never a dict, never a raise.
- **Hook callbacks must accept `**kwargs`.** `hermes plugins doctor` fails the plugin otherwise.
- **Never read secrets with `os.getenv`.** Use `agent.secret_scope.get_secret(NAME)` so a
  multiplexed process does not hand your plugin another profile's credentials.
- **Never hardcode `~/.hermes`.** Use `get_hermes_home()` in code; the home is profile-aware.
- **Never write runtime state into the plugin directory.** Use `plugin_data_dir(name)` /
  `plugin_db(name)` (`plugins/plugin_storage.py`); the install tree is replaced by update and
  deleted by remove.
- **Never park a hook callback open to a mutation of past context.** Do not rebuild the system
  prompt or swap toolsets mid-conversation — it breaks prompt caching, which the project treats
  as sacred. `pre_llm_call` injects into the *user message*, not the system prompt, for this
  reason.

## Authoritative sources in the repo

Read these rather than trusting this skill for anything load-bearing:

- `website/docs/developer-guide/plugins/index.md` — the canonical authoring guide (2300 lines).
- `website/docs/user-guide/features/plugins.md` — discovery, enabling, capabilities, injection, MCP.
- `website/docs/user-guide/features/hooks.md` — hook catalogue.
- `plugin-catalog/README.md` — catalog admission rules.
- `hermes_cli/plugins.py` — `PluginContext` (`VALID_HOOKS`, every `register_*` signature).
- `hermes_cli/plugins_manifest.py` — the manifest parser and known-field list.
- `plugins/` — ~100 bundled plugins; `plugins/security-guidance/` and `plugins/disk-cleanup/`
  are the smallest complete hook plugins, `plugins/platforms/irc/` the reference adapter.
