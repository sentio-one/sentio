---
name: hermes-plugin-commands-skills
description: "Add slash commands, CLI subcommands, and bundled skills from a Hermes plugin — ctx.register_command, ctx.register_cli_command, ctx.register_skill, automation blueprints, system-prompt sections, message injection and the plugin event bus. Use when a plugin needs a user-facing command or to ship skills."
license: MIT
---

# Hermes Plugin Commands, Skills and Injection

> Verified against source at commit `e5eb6b36` (2026-10-15). Static reading only.
> Sources: `hermes_cli/plugins.py` (`register_command` 645, `register_cli_command` 631,
> `register_skill` via `hermes_cli/plugins_content.py`, `inject_message` 601, `emit` 982,
> `subscribe` 1003), `website/docs/developer-guide/plugins/index.md`.

## Slash commands — `ctx.register_command`

In-session commands like `/mystatus`, available in the CLI and every gateway platform.

```python
def register(ctx):
    def handle(raw_args: str) -> str:
        if raw_args.strip() == "help":
            return "Usage: /mystatus [help|check]"
        return "Plugin status: all systems nominal"

    ctx.register_command("mystatus", handler=handle, description="Show plugin status")
```

Exact signature (`hermes_cli/plugins.py:645`):

```python
def register_command(self, name: str, handler: Callable, description: str = "",
                     args_hint: str = "", argument_mode: str | None = None): ...
```

| Parameter | Notes |
|---|---|
| `name` | without the leading `/`; lowercased, spaces → hyphens, a leading `/` is stripped |
| `handler` | `fn(raw_args: str) -> str \| None` — receives **everything after the command name**. May be `async def`; the gateway dispatch awaits it automatically. |
| `description` | shown in `/help`, autocomplete, and the Telegram bot menu |
| `args_hint` | e.g. `"<file>"` — lets adapters like Discord surface an argument field; without it the command registers parameterless there but still accepts trailing text |
| `argument_mode` | `"options"` \| `"text"` \| `"mixed"`; anything else falls back to `"text"` when `args_hint` is set, else `None` |

- **Built-in conflict protection:** a name that collides with a core command (`help`, `model`,
  `new`, …) is **silently rejected** with a log warning. Built-ins always win.
- The handler returns a string shown to the user, or `None`.

## CLI subcommands — `ctx.register_cli_command`

Adds a `hermes <plugin> <subcommand>` tree; it is wired into the `hermes` argparse tree at
startup, with no change to `main.py`.

```python
def _run(args):
    sub = getattr(args, "my_command", None)
    if sub == "status":
        print("All good!")
    else:
        print("Usage: hermes my-plugin <status|config>")

def _setup(subparser):
    subs = subparser.add_subparsers(dest="my_command")
    subs.add_parser("status", help="Show plugin status")
    subs.add_parser("config", help="Show plugin config")
    subparser.set_defaults(func=_run)          # <- REQUIRED: this is how the handler is bound

def register(ctx):
    ctx.register_cli_command(name="my-plugin", help="Manage my plugin",
                             setup_fn=_setup, handler_fn=_run)
```

Exact signature (`hermes_cli/plugins.py:631`):

```python
def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""): ...
```

`setup_fn` receives the argparse subparser; `handler_fn` becomes `set_defaults(func=...)`. In the
example both are provided because `_setup` binds `_run` itself.

**Slash command vs CLI command:**

| | `register_command()` | `register_cli_command()` |
|---|---|---|
| Invoked as | `/name` in a session | `hermes name` in a terminal |
| Works in | CLI, Telegram, Discord, … | terminal only |
| Handler gets | raw args string | argparse `Namespace` |
| Good for | diagnostics, quick actions | subcommand trees, setup wizards |

**Memory provider plugins** use a different convention: put `register_cli(subparser)` in the
plugin's `cli.py` and the memory discovery wires it automatically — no `ctx.register_cli_command`.
Those commands only appear while that provider is the active `memory.provider`.

## Dispatch a tool from a command

```python
def register(ctx):
    def _handle_scan(raw_args: str):
        return ctx.dispatch_tool("terminal", {"command": f"find . -name '{raw_args}'"})

    ctx.register_command("scan", handler=_handle_scan, description="Find files matching a glob")
```

`ctx.dispatch_tool(name, args, **kwargs) -> str` invokes any registered tool (built-in or
plugin, including `kanban_*`, `delegate_task`, `terminal`, `read_file`) with the parent agent's
context wired up (CLI) or gracefully degraded (gateway). The call rides the normal approval,
redaction and budget pipeline. This is the **supported** way to act from a command or hook — never
reach into `ctx._cli_ref` (it is `None` in the gateway, in `hermes chat -q`, and in
kanban-spawned workers). Two stable alternatives for those contexts: `ctx.profile_name` and
`ctx.dispatch_tool`.

## Bundle skills — `ctx.register_skill`

```
~/.hermes/plugins/my-plugin/
├── __init__.py
├── plugin.yaml
└── skills/
    ├── my-workflow/SKILL.md
    └── my-checklist/SKILL.md
```

```python
from pathlib import Path

def register(ctx):
    skills_dir = Path(__file__).parent / "skills"
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, skill_md)
```

- Registered skills are namespaced **`plugin:skill`** and loaded with
  `skill_view("my-plugin:my-workflow")`.
- They are **read-only** — they do not enter `~/.hermes/skills/` and cannot be edited via
  `skill_manage`.
- They are **not listed** in the system prompt's `<available_skills>` index — they are explicit
  opt-in loads. Because the model will not discover the name on its own, tell it the skill exists
  (in another skill, or in the user's instructions).
- A bare `skill_view("my-workflow")` still resolves the built-in version; the namespace prevents
  collisions.
- Loading a plugin skill prepends a bundle banner listing sibling skills from the same plugin.
- The legacy pattern (copying a skill into `~/.hermes/skills/` with `shutil.copy2`) still works but
  risks colliding with a built-in skill; prefer `ctx.register_skill`.

## Automation blueprints

`ctx.register_automation_blueprint(key, *, title, description, schedule_template,
prompt_template, category="general", slots=(), deliver_default="origin", skills=(), tags=())`
adds a fill-in-the-blanks automation to the Blueprint catalog; users run it with
`/blueprint <plugin>:<key>`.

```python
def register(ctx):
    ctx.register_automation_blueprint(
        "standup",
        title="Team standup digest",
        description="Every weekday, summarise what changed in a repo since yesterday.",
        category="work",
        schedule_template="{minute} {hour} * * 1-5",
        prompt_template="Summarise yesterday's commits and open PRs for {repo} as a standup digest.",
        slots=[
            {"name": "repo", "type": "text", "label": "Which repo?", "default": "acme/app"},
            {"name": "time", "type": "time", "label": "What time?", "default": "09:15"},
        ],
        tags=["work", "daily"],
    )
```

- Keys are namespaced to `<plugin>:<key>`; a bare key containing `:` is rejected.
- Slot `type` is `time` (`HH:MM`), `enum`, `weekdays`, or `text`. `schedule_template` may use slot
  names plus `{minute}`/`{hour}` (from a slot named `time`) and `{dow}`.
- An unknown placeholder, bad slot type, duplicate key or unparseable schedule logs a warning and
  skips **that blueprint**; the rest of the plugin still loads.
- Needs no manifest capability and works under host isolation (slots are plain dicts).
- Scheduling creates an ordinary cron job. Removing the plugin removes the blueprint but leaves
  those jobs running.

## System-prompt sections and the event bus

```python
ctx.register_system_prompt_section(...)   # plugins.py:955 — contributes prompt text
ctx.emit("my-event", {"k": 1})            # publish on the plugin event bus -> int (delivered count)
ctx.subscribe("other-plugin.some-event", callback)
```

Events published with `emit` are namespace-gated: a bare name publishes under `<plugin-key>:<name>`,
and `subscribe` takes fully-qualified `<plugin>:<event>` names. Owner-tagged so unload removes
zombie callbacks. Declare `emits:` / `listens:` in the manifest for discoverability — they are
**advisory only** and change no behaviour.

## Injecting messages — `ctx.inject_message`

```python
ctx.inject_message("New data arrived from the webhook", role="user")                       # active CLI
ctx.inject_message("...", role="user", session_key="agent:main:telegram:dm:123456789")     # existing gateway session
ctx.inject_message("Review the attached spec.", origin={                                  # start/continue a chat session
    "platform": "discord", "chat_id": "1290000000000000000", "chat_type": "thread",
    "thread_id": "1290000000000000000", "user_id": "123456789012345678", "user_name": "Desk Orchestrator",
})
```

Exact signature (`hermes_cli/plugins.py:601`):

```python
def inject_message(self, content: str, role: str = "user", *,
                   session_key: str | None = None,
                   origin: Mapping[str, Any] | None = None) -> bool: ...
```

- CLI: an idle agent queues it as the next input; a mid-turn agent is interrupted, exactly as if
  the user typed.
- Gateway: needs `session_key` (the **durable** routing key, not the CLI session id) or `origin`
  (a `SessionSource.to_dict()` shape; `platform` and `chat_id` required). It is disabled by
  default — grant it per plugin with `plugins.entries.<id>.allow_gateway_injection: true`.
- A plugin can never target another profile, and authorisation is rechecked: the origin's
  `user_id` must pass the gateway's current allowlists/pairing.
- Injected text is always conversational input — it cannot invoke slash commands, approve tools,
  or resolve prompts.
- Returns `True` when a live host accepted the request (async dispatch), not that the turn
  completed. `False` when neither or both of `session_key`/`origin` are given, the permission is not
  granted, or no live host can accept it.
- A gateway `origin` whose chat has **no session yet** is refused — the platform must deliver the
  first message before a plugin can start that session.
- Ink TUI / desktop chat is a third host that uses the same `session_key` (the `ses_…` id) and is
  never rerouted to a different chat.

## Quick reference

| Want | Call |
|---|---|
| `/slash` in any session | `ctx.register_command(name, handler, description, args_hint)` |
| `hermes <name>` tree | `ctx.register_cli_command(name, help, setup_fn, handler_fn)` |
| Ship skills | `ctx.register_skill(name, SKILL.md path)` |
| Scheduleable blueprint | `ctx.register_automation_blueprint(key, ...)` |
| Run a tool | `ctx.dispatch_tool(name, args)` |
| Feed the conversation | `ctx.inject_message(content, role=..., session_key=/origin=...)` |
| Prompt text | `ctx.register_system_prompt_section(...)` |
| Plugin-to-plugin events | `ctx.emit(...)` / `ctx.subscribe(...)` |
