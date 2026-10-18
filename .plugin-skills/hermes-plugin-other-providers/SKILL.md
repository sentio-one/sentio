---
name: hermes-plugin-other-providers
description: "Write Hermes provider/backend plugins — memory, context engine, model provider, image/video generation, web search, browser, terminal environment, computer-use, secret source, TTS/STT, dashboard auth, approval transport — and make host-owned LLM calls with ctx.llm. Use when a plugin replaces or adds a backend."
license: MIT
---

# Hermes Provider and Backend Plugins

> Verified against source at commit `e5eb6b36` (2026-10-15). Static reading only.
> Sources: `hermes_cli/plugins.py` (`_SCOPED_PROVIDER_REGISTRARS`, 1013–1063),
> `website/docs/developer-guide/*-plugin.md`, `website/docs/developer-guide/plugins/index.md`,
> `agent/plugin_llm.py`, `plugins/AGENTS.md`.

## Which kind, and where it lives

| Kind | Bundled path | User path | Selection key |
|---|---|---|---|
| Memory provider | `plugins/memory/<name>/` | `~/.hermes/plugins/memory/<name>/` | `memory.provider` |
| Context engine | `plugins/context_engine/<name>/` | `~/.hermes/plugins/context_engine/<name>/` | `context.engine` |
| Model provider | `plugins/model-providers/<name>/` | `~/.hermes/plugins/model-providers/<name>/` | `--provider` / `config.yaml` |
| Image generation | `plugins/image_gen/<name>/` | `~/.hermes/plugins/<name>/` | `image_gen.provider` |
| Video generation | `plugins/video_gen/<name>/` | `~/.hermes/plugins/<name>/` | `video_gen.provider` |
| Web search / extract | `plugins/web/<name>/` | `~/.hermes/plugins/<name>/` | `web.search_backend` / `web.extract_backend` / `web.backend` |
| Browser (cloud CDP) | `plugins/browser/<name>/` | `~/.hermes/plugins/<name>/` | `browser.cloud_provider` |
| Terminal environment | `plugins/…` | `~/.hermes/plugins/<name>/` | `terminal.backend` |
| Computer use | `plugins/computer_use/<name>/` | `~/.hermes/plugins/<name>/` | `computer_use.backend` |
| Secret source | `plugins/…` | `~/.hermes/plugins/<name>/` | `secrets.<name>` |
| TTS / STT | `plugins/…` | `~/.hermes/plugins/<name>/` | `tts.provider` / `stt.provider` |
| Dashboard auth | `plugins/dashboard_auth/<name>/` | `~/.hermes/plugins/<name>/` | dashboard config |

**Is `plugins.enabled` needed?** Bundled memory / context-engine / model-provider / image-gen /
platform plugins auto-load — you select them by config key. Other provider plugins (and anything
user-installed) are opt-in via `plugins.enabled`, unless the kind's own loader owns activation.
Memory providers are the exception that resolves **bundled-first** on a name collision, so a
user provider needs a unique name.

## Provider registrars on `PluginContext`

Every `register_<category>_provider(provider)` is generated from one table
(`hermes_cli/plugins.py:1018`) — it type-checks against the ABC, registers into a scope-keyed
process-global registry, and leases the slot so unload restores the displaced entry.

| Method | ABC | Registry module | Registered name is matched by |
|---|---|---|---|
| `register_image_gen_provider` | `agent.image_gen_provider:ImageGenProvider` | `agent.image_gen_registry` | `image_gen.provider` |
| `register_video_gen_provider` | `agent.video_gen_provider:VideoGenProvider` | `agent.video_gen_registry` | `video_gen.provider` |
| `register_web_search_provider` | `agent.web_search_provider:WebSearchProvider` | `agent.web_search_registry` | `web.*_backend` |
| `register_browser_provider` | `agent.browser_provider:BrowserProvider` | `agent.browser_registry` | `browser.cloud_provider` |
| `register_terminal_environment_provider` | `agent.terminal_env_provider:TerminalEnvironmentProvider` | `agent.terminal_env_registry` | `terminal.backend` (built-in names rejected — plugins never shadow in-tree backends) |
| `register_secret_source` | `agent.secret_sources.base:SecretSource` | `agent.secret_sources.registry` | `secrets.<name>` |
| `register_tts_provider` | `agent.tts_provider:TTSProvider` | `agent.tts_registry` | `tts.provider` (a `tts.providers.<name>: type: command` entry wins) |
| `register_transcription_provider` | `agent.transcription_provider:TranscriptionProvider` | `agent.transcription_registry` | `stt.provider` (a command provider wins) |

Plus hand-written registrars: `register_context_engine(engine)`, `register_memory_provider(provider)`,
`register_computer_use_provider(provider)`, `register_dashboard_auth_provider(provider)`,
`register_context_reference(provider)`, `register_approval_transport(name, present_fn)`.

---

## Memory provider — `~/.hermes/plugins/memory/<name>/`

```python
# __init__.py
from agent.memory_provider import MemoryProvider

class MyMemoryProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "my-memory"

    def is_available(self) -> bool:
        from agent.secret_scope import get_secret
        return bool(get_secret("MY_MEMORY_API_KEY"))

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id

    def sync_turn(self, user_content, assistant_content, *, session_id="", messages=None) -> None: ...
    def prefetch(self, query, *, session_id="") -> str: ...
    def get_tool_schemas(self) -> list[dict]:
        return []          # required @abstractmethod — see the full guide

    def shutdown(self) -> None: ...       # optional
    def post_setup(self, hermes_home, config) -> None: ...   # optional; `hermes memory setup`

def register(ctx):
    ctx.register_memory_provider(MyMemoryProvider())
```

- Own discovery loader (`plugins/memory/__init__.py`); exactly **one** provider is active, chosen by
  `memory.provider`. Enumerates without importing.
- Optional `cli.py` with `register_cli(subparser)` is wired by `discover_plugin_cli_commands()` —
  **only for the active provider**, so `hermes --help` stays clean.
- Never cache `hermes_home` from `initialize()` as "the" home — one process serves several profiles.
  Key state by the home handed to you per call. Never read `os.environ` for credentials
  (`agent.secret_scope.get_secret`). Start background work with
  `agent.memory_provider.spawn_context_thread`, never a bare `threading.Thread`.
- Full guide: `website/docs/developer-guide/memory-provider-plugin.md`.

## Context engine — `~/.hermes/plugins/context_engine/<name>/`

```python
from agent.context_engine import ContextEngine

class MyContextEngine(ContextEngine):
    @property
    def name(self) -> str:
        return "my-engine"

    def update_from_response(self, usage) -> None: ...
    def should_compress(self, prompt_tokens: int = None) -> bool: ...
    def compress(self, messages, current_tokens=None, focus_topic=None,
                 force=False, memory_context="") -> list: ...

def register(ctx):
    ctx.register_context_engine(MyContextEngine())
```

Single-select via `context.engine`. A second registration is rejected with a warning.
Guide: `website/docs/developer-guide/context-engine-plugin.md`.

## Model provider — `plugins/model-providers/<name>/`

```python
# __init__.py — registers at IMPORT time, not from register(ctx)
from providers import register_provider
from providers.base import ProviderProfile

register_provider(ProviderProfile(
    name="acme",
    aliases=("acme-inference",),
    display_name="Acme Inference",
    env_vars=("ACME_API_KEY", "ACME_BASE_URL"),
    base_url="https://api.acme.example.com/v1",
    auth_type="api_key",
    default_aux_model="acme-small-fast",
    fallback_models=("acme-large-v3", "acme-medium-v3"),
))
```

```yaml
# plugin.yaml
name: acme-provider
kind: model-provider
version: 1.0.0
description: Acme Inference — OpenAI-compatible direct API
```

- Lazy-discovered on the first `get_provider_profile()` / `list_providers()` call;
  `auth.py`, `config.py`, `doctor.py`, `models.py`, `runtime_provider.py` and the chat-completions
  transport auto-wire to it. **Last-writer-wins**, so a user profile overrides a bundled one.
- `PluginManager` records these manifests but does **not** import them (that would
  double-instantiate); an undeclared kind is auto-coerced from `register_provider` +
  `ProviderProfile` markers.
- Overridable hooks: `prepare_messages`, `build_extra_body`, `build_api_kwargs_extras`,
  `fetch_models`. Guide: `website/docs/developer-guide/model-provider-plugin.md`.

## Image generation — `plugins/image_gen/<name>/`

```python
from agent.image_gen_provider import ImageGenProvider

class MyImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "my-imggen"
    def is_available(self) -> bool: ...
    def generate(self, prompt: str, aspect_ratio="landscape", **kwargs) -> dict:
        # return success_response(...) / error_response(...)
        ...

def register(ctx):
    ctx.register_image_gen_provider(MyImageGenProvider())
```

```yaml
name: my-imggen
kind: backend
version: 1.0.0
description: Custom image generation backend
```

Selected via `image_gen.provider`. References: `plugins/image_gen/openai/`,
`plugins/image_gen/openai-codex/`, `plugins/image_gen/xai/`. Video generation is the same shape
with `VideoGenProvider` / `ctx.register_video_gen_provider()` / `video_gen.provider`.

## Computer-use driver — `~/.hermes/plugins/<name>/`

```python
from tools.computer_use.backend import (
    ActionResult, CaptureResult, ComputerUseBackend, ComputerUseProvider,
)

class MyBackend(ComputerUseBackend):
    def start(self): ...
    def stop(self): ...
    def is_available(self): return True
    def capture(self, mode="som", app=None, pid=None, window_id=None):
        return CaptureResult(mode=mode, width=1920, height=1080, png_b64=...)
    def click(self, **kw): return ActionResult(ok=True, action="click")
    def drag(self, **kw): return ActionResult(ok=True, action="drag")
    def scroll(self, **kw): return ActionResult(ok=True, action="scroll")
    def type_text(self, text, **kw): return ActionResult(ok=True, action="type")
    def key(self, keys, **kw): return ActionResult(ok=True, action="key")
    def list_apps(self): return []
    def focus_app(self, app, raise_window=False): return ActionResult(ok=True, action="focus_app")
    def set_value(self, value, element=None):
        # a driver that cannot do an action says so per call; the tool schema never changes
        return ActionResult(ok=False, action="set_value", code="unsupported_action", message="...")

class MyDriverProvider(ComputerUseProvider):
    name = "my-driver"
    display_name = "My driver"
    def create_backend(self, *, permission_mode):   # standard | bounded | unrestricted
        return MyBackend()
    def is_available(self): return True
    def doctor(self):                                 # optional: `hermes computer-use doctor`
        return 0

def register(ctx):
    ctx.register_computer_use_provider(MyDriverProvider())
```

Single-select via `computer_use.backend` (default `cua`). Only the selected provider is imported;
others are listed from `plugin.yaml`. The model-facing schema is identical across providers so the
prompt cache survives a driver swap — report gaps per action with `unsupported_action`. Providers
return live sessions, so they load **in-process only** (refused under host isolation).

## Secret source — `plugins/…`

```python
# subclasses agent.secret_sources.base.SecretSource
def register(ctx):
    ctx.register_secret_source(MyVaultSource())
```

Run by `load_hermes_dotenv()` after `~/.hermes/.env` and before credentials are read, when
`secrets.<name>` is enabled. The orchestrator owns ordering/precedence/provenance; your source only
fetches. Because dotenv usually loads before discovery, the manager re-pulls enabled plugin sources
afterwards. Guide: `website/docs/developer-guide/secret-source-plugin.md`.

## TTS / STT

Prefer the **config-driven** route when a CLI does the job:

```yaml
tts:
  provider: voxcpm
  providers:
    voxcpm:
      type: command
      command: "voxcpm --ref ~/voice.wav --text-file {input_path} --out {output_path}"
      output_format: mp3
```

Placeholders — TTS: `{input_path}`, `{output_path}`, `{format}`, `{voice}`, `{model}`, `{speed}`;
STT: `{input_path}`, `{output_dir}`, `{language}`, `{model}`. `HERMES_LOCAL_STT_COMMAND` is the
legacy single-command escape hatch and runs without implicit shell interpretation. A
`tts.providers.<name>: type: command` entry **wins** over a Python provider of the same name. Write
a Python provider only for SDK/streaming engines that need more than a shell template.

## Approval transport — where a human answers a prompt

```python
def present(request):
    choice = send_to_my_ui_and_wait(request)   # once/session/always/deny
    return request.respond(choice)

def register(ctx):
    ctx.register_approval_transport("my-ui", present)
```

`present` may be sync or async; Hermes runs it on a bounded worker and enforces the canonical
`approvals.timeout`. The request is immutable and carries redacted display text, the host
presentation class (`cli`/`gateway`), the host timeout, allowed choices, and an opaque
request ID/digest. Return `request.respond(choice)` — unbound dicts and stale or changed
IDs/digests are rejected, and a plugin cannot return a scope the host did not offer.

Registration alone does nothing; the operator must select it:

```yaml
plugins:
  enabled: [my-approval-plugin]
security:
  approval:
    transport: my-ui
    transport_fallback: deny     # default; `builtin` shows the prompt on the normal surface
```

Failures, timeouts, invalid choices and stale responses **deny by default**. The name `builtin` is
reserved; the regex is `[a-z0-9][a-z0-9_-]{0,63}`. There is intentionally **no** plugin approval
policy / auto-allow callback in this interface. Approval transports need in-process code (refused
under host isolation).

## Host-owned LLM calls — `ctx.llm`

`ctx.llm` borrows the user's active model and auth for a one-shot call, behind a fail-closed trust
gate. The plugin never holds the credential.

```python
def register(ctx):
    result = ctx.llm.complete(
        [{"role": "user", "content": "Summarise this."}],
        purpose="summarise",               # audit label
    )
    text, usage = result.text, result.usage

    structured = ctx.llm.complete_structured(
        instructions="Extract the fields.",
        input=[{"type": "text", "text": document}],
        json_schema={"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
    )
    if structured.content_type == "json":
        fields = structured.parsed
```

Exact signatures (`agent/plugin_llm.py`):

```text
complete(messages, *, provider=None, model=None, temperature=None, max_tokens=None,
         timeout=None, agent_id=None, profile=None, purpose=None, task=None)
complete_structured(*, instructions, input, json_schema=None, json_mode=False,
                    schema_name=None, system_prompt=None, ...same gates...)
acomplete(...) / acomplete_structured(...)      # async siblings
```

**Return / failure behaviour — this is the part that is easy to get wrong:**

- Bad or non-JSON text (with `json_mode=True` or a `json_schema`) → **no exception**:
  `parsed` is `None` and `content_type == "text"`.
- Valid JSON that **violates `json_schema`** → **raises `ValueError`**
  (`"Plugin LLM structured output did not match schema: ..."`). Wrap it in `try/except`.
- A missing `jsonschema` package skips validation with a debug log.
- `instructions` must be non-empty and `input` must have at least one block, or `ValueError` is
  raised **before** the trust gate.

Overrides (`provider`, `model`, `agent_id`, `profile`) are each gated by
`llm.provider_override` / `llm.model_override` / `llm.agent_id_override` / `llm.profile_override`
(or the deprecated `plugins.entries.<id>.llm.allow_*_override` keys). An ungranted override is
refused, not silently applied. Every call emits one INFO audit line
(`plugin_llm.<method> plugin=… provider=… model=… task=… purpose=… tokens=…`).
Guide: `website/docs/developer-guide/plugin-llm-access.md`.

## Using a user's sign-in (Codex/ChatGPT) without holding the token

Never read, refresh or rewrite `~/.codex/auth.json` or Hermes's `auth.json` — Codex refresh tokens
are single-use and a plugin that refreshes on its own races Hermes's locked refresh, revoking the
rotation chain and signing the user out. Ask Hermes to make the request:

```yaml
# plugin.yaml
requires_auth:
  - openai-codex
```

```python
from hermes_cli.plugin_provider_requests import ProviderNotSignedIn, credentialed_provider_request

def mint(session: dict) -> dict:
    try:
        resp = credentialed_provider_request(
            "openai-codex", "POST", "https://api.openai.com/v1/realtime/client_secrets",
            json={"session": session}, timeout=15)
    except ProviderNotSignedIn as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **resp.json()}
```

`credentialed_provider_request(provider, method, url, *, json=None, headers=None, timeout=30.0)`
returns a `ProviderResponse` (`status`, `headers`, `body`, `.text`, `.json()`). Rules: **declared
plugins only** (the plugin must be on the call stack — do not hand the bare helper to an executor,
or the call is refused); provider origins only (`openai-codex` sends the token only to
`https://chatgpt.com` and `https://api.openai.com`); **no redirects followed**; profile-scoped.

For usage panels use `agent.account_usage.fetch_account_usage("openai-codex", read_only=True)`.
Sign-out belongs to Hermes — point users at `hermes auth logout openai-codex`.

## Calling MCP servers from a plugin

```python
result = ctx.call_mcp("knowledge_rag", "query_knowledge", {"query": "deploy runbook"}, timeout=30)
if result["ok"]:
    ...
```

`ctx.call_mcp(server, tool, arguments=None, timeout=30) -> dict` routes through Hermes' native MCP
client (same connections, trust gates, breaker, reconnect). Returns
`{"ok": True, "result": ...}` or `{"ok": False, "error": "..."}`; results over ~64 KB are truncated
with `"truncated": True`. **Default-deny**: a server not in
`plugins.entries.<id>.mcp_allowlist` raises `PermissionError` naming the exact config key.
`"*"` wildcards are not honoured. MCP results are untrusted data, not instructions.

## Platform actions (capability-gated)

```python
result = await ctx.platform_actions.add_reaction(
    platform="telegram", chat_id="-100123", message_id="456", emoji="👍")
result = await ctx.platform_actions.set_thread_title(
    platform="discord", chat_id="123", thread_id="456", title="New title")
```

Both are async, both return a plain dict, neither raises into hook dispatch. Success is
`{"ok": True, "action": <verb>}`; failures are `{"ok": False, "error": <code>, "detail": <str>}` with
stable codes (`capability_not_granted`, `invalid_argument`, `gateway_unavailable`,
`unknown_platform`, `adapter_not_registered`, `adapter_disconnected`, `unsupported_platform_action`,
`action_failed`). v1 verbs: Telegram + Discord only, and it re-checks the
`gateway.platform_actions` capability on **every** call. This is a messaging-as-the-bot power —
grant it only to plugins you trust. (`ctx.platform_actions.host_unavailable` tells you when there is
no bridge.)
