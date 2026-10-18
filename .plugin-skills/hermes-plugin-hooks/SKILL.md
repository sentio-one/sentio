---
name: hermes-plugin-hooks
description: "Subscribe a Hermes plugin to lifecycle events — ctx.register_hook, the full VALID_HOOKS set, callback/return contracts (pre_tool_call block, pre_llm_call context injection, transform hooks), middleware, and the difference from shell and gateway hooks. Use when a plugin must run code on an agent event."
license: MIT
---

# Hermes Plugin Hooks

> Verified against source at commit `e5eb6b36` (2026-10-15). Static reading only.
> Sources: `hermes_cli/plugins.py` (the `VALID_HOOKS` set, 107–202), `hermes_cli/hooks.py`,
> `hermes_cli/middleware.py`, `website/docs/user-guide/features/hooks.md`,
> `website/docs/developer-guide/plugins/index.md`, `plugins/security-guidance/__init__.py`.

## The call

```python
def register(ctx):
    ctx.register_hook("post_tool_call", my_callback)
```

Signature: `ctx.register_hook(hook_name: str, callback: Callable) -> PluginRegistration`.

## The two rules that bite

1. **Every callback must accept `**kwargs`.** Hermes signature-inspects callbacks so a legacy
   callback receives only the fields it declares, while a `**kwargs` callback receives the full,
   additively growing payload. `hermes plugins doctor` **fails** a plugin whose hook callback does
   not accept `**kwargs` — this is a hard check, not advice.
2. **An unknown hook name is an error**, not silently ignored. Doctor reports
   `unknown hook '<name>'`. Only names in `VALID_HOOKS` are accepted.

A raising callback is logged and skipped; other hooks and the agent continue. A callback that
overruns `plugins.hook_callback_timeout` (default 30 s, 0 disables) is cut off — a timed-out
`pre_tool_call` **fails closed** (blocks the tool). Caller-thread hooks such as `subagent_stop` are
never moved onto a timeout worker.

## `VALID_HOOKS` — the complete accepted set (44 names)

The docs page says 27 events; the source has **44**. The source is authoritative.

```text
pre_tool_call                    post_tool_call
transform_terminal_output        transform_tool_result
transform_llm_output             pre_llm_call
post_llm_call
on_stream_start                  on_stream_delta
on_stream_end                    on_interim_message
pre_verify                       pre_api_request
post_api_request                 api_request_error
pre_auxiliary_call               post_auxiliary_call
transform_api_error_classification
on_session_start                 on_session_end
on_session_finalize              on_session_reset
on_skill_lifecycle               subagent_start
subagent_stop
pre_gateway_dispatch             post_gateway_admission
agent_loop_stopped
pre_approval_request             post_approval_response
on_human_input_request           on_human_input_resolved
on_room_member_activity          pre_transcription
kanban_task_claimed              kanban_task_completed
kanban_task_blocked
on_kanban_worker_spawned         on_kanban_worker_exited
on_kanban_worker_stale_claim     on_kanban_task_updated
on_kanban_dispatch_tick          gateway_platform_event
pre_command
```

`SHELL_UNSUPPORTED_HOOKS = {"transform_api_error_classification"}` — `VALID_HOOKS` doubles as the
shell-hook allow-list, so that one is refused for shell hooks (its directive has no channel there).

## Hooks whose return value steers the run

Everything else is a fire-and-forget observer whose return value is ignored.

| Hook | Callback signature | Return contract |
|---|---|---|
| `pre_tool_call` | `tool_name, args, task_id` | `{"action": "block", "message": ...}` vetoes the call. `{"action": "approve", "message": ...}` escalates to the human-approval gate. `None` / anything else lets it through. |
| `pre_llm_call` | `session_id, user_message, conversation_history, is_first_turn, model, platform` | `{"context": "..."}` or a plain non-empty string → text is **appended to the current turn's user message**. `None` → no injection. |
| `transform_tool_result` | `tool_name, args, result` (+ context) | a replacement result **string** (first non-`None` wins), else `None`. |
| `transform_llm_output` | returns a replacement string (first non-`None` wins) or `None` |
| `transform_terminal_output` | rewrite terminal output |
| `pre_verify` | once per turn when the agent edited code and is about to verify/finish. `{"action": "continue", "message": ...}` (or Claude-Code `{"decision": "block", "reason"}`) keeps it going; anything else finishes. Bounded by `agent.max_verify_nudges`. |
| `transform_api_error_classification` | once per failed API call, **before** core classifies it. Kwargs `provider, model, status_code, error_type, error_code, error_message, error_body, error, approx_tokens, context_length, num_messages`. Return `None` or `{"reason": <FailoverReason> (required), "retryable"/"should_compress"/"should_rotate_credential"/"should_fallback": bool, "message": str, "error_context": dict}`. Run-all-then-pick-first. **Privacy: `error_message`/`error_body` may be unredacted.** |
| `pre_gateway_dispatch` | once per inbound `MessageEvent`, after the internal-event guard, **before** auth/pairing. Kwargs `event, gateway, session_store`. Return `{"action": "skip", "reason"}` to drop, `{"action": "rewrite", "text"}` to replace the text, `"allow"`/`None` for normal. |
| `pre_transcription` | after provider resolution, **before** any backend runs. Kwargs `file_path, provider, model, language, prompt, source`. Return `None` or a dict mutating `prompt`/`language`/`model` (registration order, last-writer-wins; `file_path` is read-only). |
| `pre_command` | before a recognised slash command's handler. Kwargs `surface, command (canonical), alias_used, args_raw, session_key, platform`. **Return values are IGNORED in v1.** Deliberately not fired for the gateway's running-agent intercept path (`/stop`, `/approve`, busy_policy). |

### `pre_llm_call` injection details

- Injected into the **user message**, not the system prompt — deliberately, to keep the cached
  system-prompt prefix stable (prompt caching is treated as sacred in this project).
- Ephemeral: the stored user message is never mutated and nothing is persisted.
- Multiple plugins' context is joined with blank lines, ordered by plugin discovery order.
- Per-hook context is capped at **10,000 chars** by default; overflow is written to
  `$HERMES_HOME/hook_outputs/<session_id>/<uuid>.txt` and replaced with a head/tail preview plus
  the path. Tune under `hooks.output_spill` (`enabled`, `max_chars`, `preview_head`,
  `preview_tail`, `directory`).

```python
def register(ctx):
    def recall(session_id, user_message, **kwargs):
        try:
            memories = fetch(session_id, user_message)
        except Exception:
            return None                      # fail silently — never break the turn
        return {"context": "Recalled:\n" + "\n".join(memories)} if memories else None

    ctx.register_hook("pre_llm_call", recall)
```

### `pre_tool_call` block example

```python
def register(ctx):
    def guard(tool_name="", args=None, **kwargs):
        run = ctx.current_cron_execution()
        if tool_name == "deploy" and (run is None or run.scheduled_instant is None):
            return {"action": "block", "message": "deploy only runs from its scheduled cron job"}
    ctx.register_hook("pre_tool_call", guard)
```

## Observer payloads worth knowing

| Hook | Payload |
|---|---|
| `post_tool_call` | `tool_name, args, result, task_id, duration_ms` |
| `pre_api_request` | `session_id, model, provider, base_url, api_mode, api_call_count, message_count, tool_count, approx_input_tokens, max_tokens, request` — sanitized, size-capped JSON |
| `post_api_request` | the above plus `api_duration, finish_reason, response_model, usage, response, assistant_content_chars, assistant_tool_call_count` |
| `api_request_error` | correlation fields plus `status_code, retry_count, max_retries, retryable, reason, error, request` |
| `pre_auxiliary_call` / `post_auxiliary_call` | the `*_api_request` shape **plus `aux_task`** — titling, compression, MoA, vision, approval. Distinct events so turn-scoped `*_api_request` subscribers never see auxiliary traffic. |
| `on_session_start` | `session_id, model, platform` |
| `on_session_end` | `session_id, completed, interrupted, model, platform` |
| `on_session_finalize` / `on_session_reset` | `session_id`, `platform` |
| `agent_loop_stopped` | `session_key, platform, reason, invalidation_reason` |
| `pre_approval_request` / `post_approval_response` | `command, description, pattern_key, pattern_keys, session_key, surface` (`cli`/`gateway`/`smart`/`mcp-elicitation/<server>`/`mcp-trust/<server>`/`vault-payment`); post adds `choice, decided_by` |
| `on_skill_lifecycle` | successful skill lifecycle facts (local skill name visible to plugins) |
| `kanban_task_*` | `task_id, board, assignee, run_id, profile_name` (+ `summary` on completed, `reason` on blocked). Fire **after** the DB commit; claimed fires in the dispatcher, completed/blocked in the worker process. |

Every API-request payload carries `turn_id`, `api_request_id`, `task_id`, `session_id` and
`api_call_count` so a tracing plugin can stitch requests, tool calls and turns together.

## Middleware — when observing is not enough

Hooks observe (with the few steering shapes above); **middleware changes what happens**.
`VALID_MIDDLEWARE` in `hermes_cli/middleware.py`:

```python
def cap_find_output(tool_name, args, **kwargs):
    if tool_name == "terminal" and args.get("command", "").startswith("find "):
        return {"args": {**args, "command": args["command"] + " | head -100"},
                "source": "my-plugin", "reason": "cap find output"}
    return None   # leave unchanged

def register(ctx):
    ctx.register_middleware("tool_request", cap_find_output)
```

| Kind | Receives | Return |
|---|---|---|
| `tool_request` | `tool_name, args, original_args`, context | `{"args": {...}}` replaces the effective args before hooks/guardrails/approvals/execution. `None` = unchanged. |
| `llm_request` | `request, original_request`, context | `{"request": {...}}` replaces the effective provider kwargs. |
| `tool_execution` | payload + `next_call` | wrappers call `next_call(payload)` **exactly once** (twice raises) and return the result. |
| `llm_execution` | payload + `next_call` | same shape, wrapping the provider call. |

Request middleware chains: each callback sees the previous rewrite, while `original_args` /
`original_request` always carries the pre-middleware copy. `source`, `reason`, `name` land in the
`middleware_trace` kwarg that observer hooks receive. A raising callback is logged and skipped.
Unknown kinds register with a warning (forward compatibility). Payloads carry
`middleware_schema_version = "hermes.middleware.v1"`.

## Related but different hook systems

Do not confuse plugin hooks with:

- **Shell hooks** — `hooks:` entries in `config.yaml`; run a shell command on an event, no Python.
  Same event names as the Python set plus `pre_gateway_dispatch`, and structured JSON output for
  `pre_tool_call` blocking.
- **Gateway event hooks** — `~/.hermes/hooks/<name>/HOOK.yaml` + `handler.py`. Events are
  `gateway:startup`, `session:start`, `session:end`, `session:reset`, `agent:start`, `agent:step`,
  `agent:end`, `command:*`. They are **not** gated by `plugins.enabled` — placing the files is the
  opt-in.
- **Outbound webhooks** — a separate config surface.

## A complete worked hook plugin

`plugins/security-guidance/__init__.py` in the repo is the smallest real example: it registers
`pre_tool_call` (block mode) and `transform_tool_result` (warn mode), both callbacks ending in
`**_: Any`. Copy its shape:

```python
def _on_pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict[str, str]]:
    findings = scan(tool_name, args)
    return {"action": "block", "message": "..."} if findings else None

def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
```

## Verifying

```bash
hermes plugins doctor ~/.hermes/plugins/my-plugin --ci
```

Doctor errors on an unknown hook name and on any hook callback lacking `**kwargs`, and warns when
`provides_hooks` and the registered hooks disagree.
