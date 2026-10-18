---
name: hermes-plugin-tools
description: "Add a tool the Hermes model can call from a plugin — ctx.register_tool signature, handler contract (JSON string, never raise), tool schemas, check_fn availability, async handlers, overriding built-ins, and dispatching other tools. Use when a plugin needs to expose a tool."
license: MIT
---

# Hermes Plugin Tools

> Verified against source at commit `e5eb6b36` (2026-10-15). Static reading only.
> Sources: `hermes_cli/plugins.py` (`PluginContext.register_tool`), `tools/registry.py`
> (`registry.register`), `tools/AGENTS.md`, `website/docs/developer-guide/plugins/index.md`.

## The call

```python
ctx.register_tool(
    name="my_tool",          # the model-facing tool name
    toolset="my_toolset",    # groups the tool; the toggle unit in `hermes tools`
    schema={...},            # what the model reads — REQUIRED, must be a dict
    handler=my_handler,
    check_fn=None,           # optional: False hides the tool from the model
    requires_env=None,       # optional: list of env var names (metadata + requirements UI)
    is_async=False,          # set True for `async def` handlers
    description="",          # optional ToolEntry metadata; defaults to schema["description"]
    emoji="",                # optional
    override=False,          # replace a same-named tool (needs operator grant, see below)
)
```

Exact signature (`hermes_cli/plugins.py:453`):

```python
def register_tool(self, name, toolset, schema, handler, check_fn=None,
                  requires_env=None, is_async=False, description="",
                  emoji="", override=False) -> Optional[PluginRegistration]: ...
```

The registry call underneath is richer (`tools/registry.py:709`) and also accepts
`max_result_size_chars`, `dynamic_schema_overrides`, `execution_timeout` and `scope` — plugin
authors normally go through `ctx.register_tool`, which supplies `scope`.

## Handler contract — the part people get wrong

```python
def my_handler(args: dict, **kwargs) -> str:
    """args   — the parameters the model passed
    kwargs — Hermes-injected context (task_id, session_id, parent_agent, ...)"""
    try:
        return json.dumps({"success": True, "result": ...})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
```

1. **Always return a JSON string** — success *and* error. Never a dict, never `None`.
2. **Never let an exception escape.** Catch and return error JSON; an uncaught raise fails the
   tool call.
3. **Accept `**kwargs`.** Hermes signature-inspects and only forwards the context keywords your
   signature names, so `def handler(args)` technically works — but `**kwargs` is how you opt into
   the full, additively growing context (`task_id`, `session_id`, `user_task`, `parent_agent`, …).
4. **Async handlers:** declare `async def` and pass `is_async=True`. Do not call `asyncio.run()`
   yourself — Hermes owns the loop.
5. **Per-session state** uses `kwargs.get("task_id")`.
6. **Never read credentials with `os.getenv`** inside a handler — use
   `agent.secret_scope.get_secret(NAME)` so a multiplexed process does not leak another profile's
   key.

### Pixels instead of text

A vision tool may return the multimodal envelope instead of a JSON string:

```python
{"_multimodal": True, "content": [text, image_url, ...], "text_summary": str}
```

`registry._normalize_handler_result` accepts exactly that shape. Anything else must be a JSON
string.

## Tool schema

```python
{
    "name": "my_tool",            # optional — see note below
    "description": (
        "What it does AND when to use it. Be specific — this is how the model "
        "decides to call it. Supports: ..."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for"},
        },
        "required": ["query"],
    },
}
```

- `schema` must be a dict and `parameters` must be a JSON-Schema **object**, or registration
  raises `ValueError` immediately (a list here would 400 the whole turn far from your plugin).
- **`schema["name"]` is not required.** The registry builds the model-facing definition as
  `{**entry.schema, "name": entry.name}` (`tools/registry.py:900`), so the `name=` argument wins and
  a `name` key inside the schema is simply overwritten. Every official example includes it anyway;
  keep it in sync to avoid confusing readers, but nothing breaks without it.
- Two description slots exist: the model sees `schema["description"]`. The optional
  `description=` kwarg is `ToolEntry` registry metadata only; Hermes does **not** copy it back into
  a schema that lacks a description. Prefer defining the text once, in the schema.
- **Do not name tools from other toolsets inside a description** (no "prefer `web_search`") — those
  tools may be unavailable and the model will hallucinate the call. Cross-references are added
  dynamically by core.
- Paths mentioned in a description use `display_hermes_home()` (display only). State paths use
  `get_hermes_home()` **at call time**, never a module constant.
- No `offset`/`limit` on instructional tools — models read page 1 and skip the rest.

## Conditional availability — `check_fn`

```python
ctx.register_tool(..., check_fn=lambda: _has_optional_lib())
```

- `False` hides the tool from the model for that profile.
- Results are **TTL-cached process-wide**, keyed by profile (`hermes_home_key()`).
- `check_fn` answers **reachability / opt-in** — never "which surface am I on". A GUI-only tool
  belongs in a named toolset (`desktop_ui`, `project`) folded in per-platform, not behind an env
  check.
- Never install dependencies from a frequently polled `check_fn`. Use `pm.ensure_import("<extra>")`
  at the operation that needs the SDK, or `pm.available` for a read-only probe.
- `requires_env` is **declaration metadata** (it surfaces in requirements UI) — it does **not**
  gate availability. `check_fn` does.

## Overriding a built-in tool

```python
ctx.register_tool(name="browser_navigate", toolset="plugin_my_browser",
                  schema={...}, handler=my_navigate, override=True)
```

- Without `override=True`, a name already claimed by **another toolset** is rejected (logged,
  returns `None`) — this prevents accidental shadowing.
- With `override=True` against a **built-in**, a non-bundled plugin additionally needs the
  operator grant `plugins.entries.<id>.allow_tool_override: true` (or the `tools.override`
  capability, which the consent flow records). Without it, `register_tool` raises
  `PluginToolOverrideError` — and since the loader catches `register()` exceptions, the whole
  plugin is disabled.
- **Bundled plugins are exempt** — an override there is a maintainer decision
  (`ctx.has_capability("tools.override")` returns `True` for bundled source).
- Overrides are logged at INFO so they are auditable in `~/.hermes/logs/agent.log`.
- Same-toolset re-registration (MCP reconnect/refresh) is allowed without `override`.
- The same grant gates `deregister()`: a plugin cannot remove a tool it does not own.

## Registering a tool outside `register()`

A tool also reaches the model if you register it directly at import time, but the plugin path is
preferred. There is no per-plugin toolset table to edit — a plugin's toolset is discovered
automatically, and `toolsets.py` is only for **core** tools:

```python
# core tools only, NOT plugins:
registry.register(name=..., toolset=..., schema=..., handler=..., check_fn=..., requires_env=[...])
```

## Dispatching other tools from your plugin

```python
result = ctx.dispatch_tool("terminal", {"command": "ls -la"})   # -> the handler's JSON string
```

Signature: `ctx.dispatch_tool(tool_name: str, args: dict, **kwargs) -> str`. The parent agent is
resolved automatically in CLI mode (workspace hints, spinner, model inheritance); in gateway mode
there is no CLI agent so it degrades gracefully. The dispatched tool rides the normal approval,
redaction and budget pipelines — it is a real invocation, not a shortcut. This is the supported
way for a slash-command or hook to act; do not reach into `ctx._cli_ref`.

## Verifying

```bash
hermes plugins doctor ~/.hermes/plugins/my-plugin --ci
```

Doctor loads the plugin through the real runtime under a temp `HERMES_HOME` and reports
registration failures and declared-vs-registered drift (`provides_tools`). See
`hermes-plugin-dev-workflow`.

## Common mistakes

```python
# WRONG — returns a dict
def handler(args, **kwargs):
    return {"result": 42}

# RIGHT
def handler(args, **kwargs):
    return json.dumps({"result": 42})

# WRONG — lets the exception escape
def handler(args, **kwargs):
    return json.dumps({"r": 1 / int(args["value"])})

# RIGHT
def handler(args, **kwargs):
    try:
        return json.dumps({"r": 1 / int(args.get("value", 0))})
    except Exception as e:
        return json.dumps({"error": str(e)})
```
