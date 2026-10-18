---
name: hermes-plugin-platform-adapters
description: "Add a messaging gateway channel to Hermes as a plugin — BasePlatformAdapter, ctx.register_platform, env-driven auto-config, cron delivery, token locks, inbound dedup, native platform handlers and Slack actions. Use when a plugin must connect Hermes to a chat service."
license: MIT
---

# Hermes Plugin Platform Adapters

> Verified against source at commit `e5eb6b36` (2026-10-15). Static reading only.
> Sources: `website/docs/developer-guide/adding-platform-adapters.md`,
> `gateway/platforms/base.py`, `gateway/platforms/_shared.py`, `gateway/platform_registry.py`,
> `hermes_cli/plugins.py` (`register_platform` 775, `register_platform_handler` 827).

## Layout

```
~/.hermes/plugins/my-platform/
├── plugin.yaml     # kind: platform + metadata
├── adapter.py      # the adapter class + register(ctx)
└── tools.py        # OPTIONAL outbound client tools (see provides_tools)
```

`kind: platform` plugins are **deferred**: the adapter module (and its SDK imports) load only when
a gateway, cron, or `send_message` path first asks the platform registry for that platform. Keep
`__init__.py` import-light and pull the adapter in from inside `register()`.

## Manifest

```yaml
name: my-platform
label: My Platform
kind: platform
version: 1.0.0
description: My custom messaging platform adapter
requires_env:
  - MY_PLATFORM_TOKEN                 # bare string works
  - name: MY_PLATFORM_CHANNEL
    description: "Channel to join"
    prompt: "Channel"
    password: false
optional_env:
  - name: MY_PLATFORM_HOME_CHANNEL
    description: "Default channel for cron delivery"
    password: false
```

`requires_env` / `optional_env` entries auto-populate `OPTIONAL_ENV_VARS`, so they appear in
`hermes config` and the setup UI for free. Supported dict keys: `name`, `description`, `prompt`,
`url`, `password`/`secret` (bool; auto-detected from a `_TOKEN`/`_SECRET`/`_KEY`/`_PASSWORD`/`_JSON`
suffix when omitted), `category` (default `messaging`). A hardcoded entry for the same var already
in `OPTIONAL_ENV_VARS` wins.

### Outbound client tools — `provides_tools`

If the plugin also ships tools the agent should call from **any** session, put them in `tools.py`
with a `register_tools(ctx)` function and declare them:

```yaml
provides_tools:
  - my_platform_call
  - my_platform_list
```

With `provides_tools` declared, plugin discovery imports **only** `tools.py` in every process —
CLI and TUI included — while the adapter stays deferred. Without it the whole plugin stays
deferred. Users enable the toolset per platform (`hermes tools enable my_platform --platform cli`,
or `platform_toolsets` in `config.yaml`).

## The adapter

```python
from gateway.platforms._shared import extra_or_secret, get_scoped_secret, seed_extra_from_env
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType
from gateway.config import Platform, PlatformConfig


class MyPlatformAdapter(BasePlatformAdapter):
    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("my_platform"))
        # explicit env (profile-scoped) → this profile's config.extra (YAML) → default
        self.token = extra_or_secret(config.extra, "token", "MY_PLATFORM_TOKEN")

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        # ... connect, start listeners ...
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        # ... send via the platform API ...
        return SendResult(success=True, message_id="...")

    async def get_chat_info(self, chat_id):
        return {"name": chat_id, "type": "dm"}
```

`BasePlatformAdapter` (`gateway/platforms/base.py`) requires **`connect()`**, **`disconnect()`**
and **`send()`**; **`send_typing()`** and **`get_chat_info()`** are optional overrides. Call
`self._mark_connected()` / `self._mark_disconnected()` to update adapter state.

Inbound messages: build a `MessageEvent` and hand it to the base class, which routes it to the
gateway runner:

```python
source = self.build_source(chat_id=chat_id, chat_name=name, chat_type="dm",
                           user_id=user_id, user_name=user_name)
await self.handle_message(MessageEvent(text=content, message_type=MessageType.TEXT,
                                       source=source, message_id=msg_id))
```

## `register(ctx)` — the full `register_platform` surface

```python
def register(ctx):
    ctx.register_platform(
        name="my_platform",
        label="My Platform",
        adapter_factory=MyPlatformAdapter,          # or lambda cfg: MyPlatformAdapter(cfg)
        check_fn=check_requirements,                # PASSIVE probe — must NEVER pip-install
        validate_config=validate_config,
        required_env=["MY_PLATFORM_TOKEN"],
        install_hint="Declare my-platform-sdk in this plugin's Python dependencies, then retry",
        # ensure_deps_fn=ensure_requirements,       # ACTIVE installer for a lazy SDK (optional)
        env_enablement_fn=_env_enablement,          # seed PlatformConfig.extra from env
        apply_yaml_config_fn=_apply_yaml_config,    # bridge config.yaml keys -> env/extras
        cron_deliver_env_var="MY_PLATFORM_HOME_CHANNEL",
        allowed_users_env="MY_PLATFORM_ALLOWED_USERS",
        allow_all_env="MY_PLATFORM_ALLOW_ALL_USERS",
        max_message_length=4000,                    # smart chunking; 0 = no limit
        platform_hint="You are chatting via My Platform. It supports markdown.",
        emoji="💬",
        # standalone outbound send (see below)
        parse_target_ref_fn=_parse_address,
        validate_target_ref_fn=_validate_address,
        send_message_handler=_send_request,
        standalone_sender_fn=_standalone_send,
    )
```

`check_fn` is a **passive** probe called from status displays and config loading — it must answer
"are deps/config present right now?" and must never install anything. `ensure_deps_fn` (optional)
is the active installer, called only when `check_fn` returns `False`, right before the gateway
connects. Omit it and a `False` `check_fn` is a hard block.

The plugin system then handles, with no core edits: adapter creation (registry is consulted before
the built-in table), config parsing, user authorization, env-only auto-enable, the YAML→env bridge,
cron delivery, `hermes config` UI entries, the send engine, webhook cross-platform delivery,
channel-directory enumeration, system-prompt hints, message chunking, PII redaction, `hermes
status`, `hermes gateway setup`, `hermes tools`/`hermes skills` per-platform config, and the
per-platform token lock.

## Credentials — never `os.getenv`

Under `gateway.multiplex_profiles` the process env holds the **default** profile's values, so a raw
read enables your platform with the wrong profile's credentials. Read through the scoped helpers in
`gateway.platforms._shared`:

```python
from gateway.platforms._shared import get_scoped_secret, seed_extra_from_env, apply_yaml_bridge

def check_requirements() -> bool:
    return bool(get_scoped_secret("MY_PLATFORM_TOKEN"))

def _env_enablement() -> dict | None:
    token = get_scoped_secret("MY_PLATFORM_TOKEN", "").strip()
    if not token:
        return None
    return {"token": token, **seed_extra_from_env(
        (("MY_PLATFORM_PORT", "port", int), ("MY_PLATFORM_NICK", "nick", None)),
        home_env="MY_PLATFORM_HOME_CHANNEL",
    )}

_YAML_BRIDGE = (
    ("require_mention", "MY_PLATFORM_REQUIRE_MENTION", "lower"),
    ("allowed_channels", "MY_PLATFORM_ALLOWED_CHANNELS", "csv"),
)

def _apply_yaml_config(yaml_cfg: dict, platform_cfg: dict) -> dict | None:
    return apply_yaml_bridge(platform_cfg, _YAML_BRIDGE)
```

- `seed_extra_from_env(table, home_env=...)`: each entry is `(ENV_VAR, extra_key, conv)`; blank
  values are skipped, a `conv` raising `ValueError` drops the key. `home_env` seeds the special
  `home_channel` dict.
- `apply_yaml_bridge` writes an env var only when it is unset (env > YAML) and **never** while a
  multiplexed secondary profile's config is loading. Kind values: `lower` (booleans →
  `"true"`/`"false"`), `str` (skips null/blank), `csv`, `json`. The hook runs after the generic
  shared-key loop and before env overrides; exceptions are swallowed and logged at debug.
- Platform plugins never mutate `os.environ` with their own values.

## Cron delivery

`cron_deliver_env_var` makes `deliver=my_platform` a recognised target. To make the actual send work
when the cron job runs **outside** the gateway process:

```python
async def _standalone_send(pconfig, chat_id, message, *, thread_id=None,
                           media_files=None, force_document=False):
    # open an ephemeral connection, send, close
    return {"success": True, "message_id": "..."}   # or {"error": "..."}
```

Without it, a cron-side send fails with `No live adapter for platform '<name>'` because built-in
platforms ship direct REST helpers in `tools/send_message_tool.py` but a plugin has no live gateway
reference outside the gateway process. Exceptions are caught and reported as
`Plugin standalone send failed: <reason>`. References: `plugins/platforms/{irc,teams,google_chat}/adapter.py`.

**`send_message` is deliberately not an agent-callable tool.** Plugins must not register an
equivalent model surface that lets the agent initiate outbound messages on its own.

## Service adapters — three optional fields

For an adapter that bridges a *service* rather than a chat network (Home Assistant is the
reference consumer):

| Field | Default | Effect |
|---|---|---|
| `trusted_inbound` | `False` | Every inbound event comes from the authenticated service; there is no human sender, so user allowlists and DM pairing do not apply. **Never set this for a chat platform** — anyone who can message the bot could drive the agent. |
| `display_tier` | `''` | `'high'`/`'medium'`/`'low'`/`'minimal'` — the built-in per-platform display defaults tier (tool progress, streaming, interim messages) when the user has no `display.platforms.<name>` override. |
| `shared_env_prefixes` | `()` | Env prefixes shared with a non-channel capability of the same plugin (Home Assistant: `('HASS_',)`). Profile clones strip keys under these prefixes only when the source profile actually runs the adapter. |

Feature-detect them so the plugin still loads on older cores:

```python
import dataclasses
from gateway.platform_registry import PlatformEntry

known = {f.name for f in dataclasses.fields(PlatformEntry)}
kwargs = dict(name="my_service", label="My Service", adapter_factory=MyServiceAdapter,
              check_fn=check_requirements)
for key, value in (("trusted_inbound", True), ("display_tier", "minimal"),
                   ("shared_env_prefixes", ("MY_SERVICE_",))):
    if key in known:
        kwargs[key] = value
ctx.register_platform(**kwargs)
```

## Native platform handlers (any platform)

To receive platform events core does not route — extra update types, button callbacks, reactions,
member events, webhook routes — register a factory the adapter invokes at connect time:

```python
def register(ctx):
    def _wire(native, adapter):
        from telegram.ext import CallbackQueryHandler   # import SDKs HERE, not at module level

        async def _on_button(update, context):
            await update.callback_query.answer()
            # ... handle "myplugin:*" callbacks ...

        application.add_handler(CallbackQueryHandler(_on_button, pattern=r"^myplugin:"))

    ctx.register_platform_handler("telegram", _wire)      # ctx.register_telegram_handler(_wire) is an alias
```

`ctx.register_platform_handler(platform: str, factory: Callable) -> None`. `native` per platform:

| Platform | `native` |
|---|---|
| `telegram` | PTB `Application` — `add_handler` |
| `discord` | `discord.ext.commands.Bot` — `add_listener` |
| `slack` | `slack_bolt.AsyncApp` — `app.event()` / `app.action()` / `app.command()` |
| `matrix` | Matrix client |
| `teams` | Teams `App` — `on_message` / `on_card_action` |
| `dingtalk` | `DingTalkStreamClient` — `register_callback_handler` |
| `feishu` | lark_oapi client |
| `line`, `api_server`, `msgraph_webhook` | aiohttp `web.Application` — `router.add_get/post` |
| everything else | `None` — work through the `adapter` handle |

- Factories are queued at load and invoked when the platform connects; where dispatch order matters
  (Telegram, Slack, Teams, aiohttp routers) they run **before** core handlers.
- **Always scope handlers in first-match dispatch tables** — on Telegram use
  `CallbackQueryHandler(..., pattern=r"^myplugin:")`; an unscoped handler would swallow core button
  flows (exec approvals, model picker, clarify prompts).
- A raising factory is logged and the platform still connects. One plugin may register factories
  for several platforms.
- Late-loaded plugins have their factories wired live via `rewire_plugin_handlers()` (deduped per
  native client).

**Slack Block Kit buttons** have a dedicated door:

```python
def register(ctx):
    async def _on_approve(ack, body, action):
        await ack()                                   # within 3 s — slack_bolt requirement
        sweep_id = (action.get("value") or "").split("|", 1)[-1]
        ...
    ctx.register_slack_action_handler("inbox_sweep_approve", _on_approve)
```

`ctx.register_slack_action_handler(action_id, callback)` — `action_id` may be a literal, a compiled
regex, or a constraint dict (whatever `slack_bolt.App.action()` accepts); `callback` is async and
receives `(ack, body, action)`. A raising handler is logged and best-effort-acked so Slack stops
retrying.

## Reliability patterns

**Inbound dedup** — platforms redeliver (websocket resumes, webhook retries, unacked poll batches):

```python
from gateway.platforms.helpers import MessageDeduplicator

self._dedup = MessageDeduplicator(ttl_seconds=600)   # in __init__
...
if self._dedup.is_duplicate(msg_id):                  # in the inbound handler
    return
```

On reconnect the gateway copies every `MessageDeduplicator` attribute's live IDs to the new
instance, so a replay right after a reconnect is still dropped. A plain dict/set cache does not get
that treatment.

**Token lock** — a persistent connection with a unique credential must not be used by two profiles:

```python
from gateway.status import acquire_scoped_lock, release_scoped_lock

async def connect(self, *, is_reconnect: bool = False):
    acquired, _existing = acquire_scoped_lock("my_platform", self._token)
    if not acquired:
        logger.error("Token already in use by another profile")
        return False
    ...
async def disconnect(self):
    release_scoped_lock("my_platform", self._token)
```

**Slow-LLM UX** — override `_keep_typing` (always `await super()._keep_typing(...)` and tear your
side task down in `finally`) and/or `send`. `_SYSTEM_BYPASS_PREFIXES` (`⚡`, `⏳`, `⏩`, `💾`) are
gateway busy-acks — always let those through visibly. Identifier: `plugins/platforms/line/adapter.py`.

## Configuration and verification

```yaml
# config.yaml
gateway:
  platforms:
    my_platform:
      enabled: true
      extra:
        token: "..."
        channel: "#general"
```

```bash
hermes gateway setup        # plugin platforms appear in the menu
hermes gateway status
hermes status               # lists plugin platforms alongside built-ins
```

**Bundled platform plugins auto-load** (no `plugins.enabled` entry needed) and the channel turns on
via `gateway.platforms.<name>.enabled`. A **user-installed** platform under
`~/.hermes/plugins/platforms/` is third-party code and **is** gated by `plugins.enabled`.

**Isolation caveat:** platform adapters need in-process code. Under `plugins.isolation: host`,
`register_platform` is refused with a clear reason — run those plugins with
`isolation: in_process`.

**References:** `plugins/platforms/irc/` (complete, stdlib-only, zero deps), `plugins/platforms/teams/`
(Bot Framework / Adaptive Cards), `plugins/platforms/google_chat/` (OAuth REST), `plugins/platforms/line/`
(webhook + slow-LLM UX).
