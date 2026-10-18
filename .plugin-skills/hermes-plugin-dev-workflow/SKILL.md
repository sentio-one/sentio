---
name: hermes-plugin-dev-workflow
description: "Develop, validate, test, package and run a Hermes plugin — plugins doctor, validate, install/update/remove, dependency consent, install security scanning, host isolation, plugin storage/config/state, lazy singletons and secrets. Use when testing or shipping a plugin."
license: MIT
---

# Hermes Plugin Dev Workflow

> Verified against source at commit `e5eb6b36` (2026-10-15). Static reading only.
> Sources: `hermes_cli/plugin_dev.py` (doctor), `hermes_cli/plugin_validate.py`,
> `plugins/plugin_storage.py`, `plugins/plugin_utils.py`, `website/docs/developer-guide/plugins/index.md`,
> `website/docs/user-guide/features/plugins.md`, `tests/AGENTS.md`.

## The two validators

```bash
hermes plugins doctor ~/.hermes/plugins/my-plugin --ci      # runtime, temp HERMES_HOME
hermes plugins validate /path/to/my-plugin --install-deps    # what catalog CI runs
```

**Doctor** runs the *real* runtime contract, not a parallel scanner: directory discovery, the
manifest parser, the namespaced import, `register(ctx)`, the hook registry and the tool registry,
under a temporary `HERMES_HOME` with outbound socket connects **blocked**. It errors on:

- a manifest that discovery cannot find, or more than one manifest
- `provides_hooks` / `provides_tools` that are not lists, or non-string entries
- an **unknown hook name** in `provides_hooks` or registered at runtime
- **any hook callback that does not accept `**kwargs`** — a hard error, the most common surprise
- a `register()` that raises, produces no runtime record, or does not enable the record

and warns on `manifest_version` newer than supported, invalid `api_version`, malformed
`requires_plugins`, unpinned `python_dependencies`, uninstalled declared deps, unknown
`config_schema` types, declared-vs-registered tool/hook drift, and a stale Desktop copy of
`desktop/plugin.js`.

Doctor is **not a sandbox** — plugin code executes in-process with your permissions and can spawn
subprocesses. Only run it on code you trust enough to import.

**`hermes plugins validate`** checks the manifest and `requires_hermes`, that the plugin loads, that
declared `capabilities` match what actually registers, `config_schema` and `requires_env`, Python
dependencies against Hermes' core constraints, the install security scan, and the `desktop surface`
and `no core override` rules. It rejects `plugin.yaml` with no `__init__.py`, `desktop/plugin.js` or
`plugin.json` beside it.

## Managing plugins

```bash
hermes plugins                              # interactive toggle UI
hermes plugins list                         # enabled / disabled / not-enabled table
hermes plugins search <term>                # search the Hermes plugin catalog
hermes plugins browse                       # browse every catalog entry
hermes plugins info <name>                  # full details for one entry
hermes plugins install <catalog-name>       # install a catalog entry at its pinned SHA
hermes plugins install owner/repo           # install from Git, then prompt Enable? [y/N]
hermes plugins install owner/repo --enable / --no-enable
hermes plugins install owner/repo --ref <40-char commit sha>   # reproducible pin
hermes plugins update my-plugin             # local edits autostashed and re-applied
hermes plugins remove my-plugin
hermes plugins enable / disable my-plugin
hermes plugins capabilities [my-plugin]     # declared vs granted
hermes plugins check-updates                # read-only
hermes plugins adopt my-plugin              # start tracking a self-cloned dir
hermes plugins trust-update-url my-plugin   # approve a changed update_url
hermes plugins pack show|install|export ./hermes-pack.yaml
```

- **A pin must be a full 40-character commit SHA.** Tags, branches and abbreviated SHAs are
  rejected. Hermes checks out detached and verifies `HEAD` matches. `update` refuses to move a
  pinned plugin; use `install <source> --force --ref <new-sha>`.
- Install metadata lives in `plugins/.install-metadata.json` under the profile; it contains no
  config values, env values, secrets or capability grants.
- Private repos: every clone is attempted **anonymously first**; only on refusal does Hermes look for
  `GITHUB_TOKEN`/`GH_TOKEN` in `.env`, then the `gh` CLI login, then `git credential fill`. The
  credential is one-shot and never written into the plugin's `.git/config`.
- `plugins.auto_apply: true` opts tracked Git plugins into unattended updates; `plugins.auto_update_check_hours`
  (default 24, 0 disables) controls the scheduled check.

## Debugging

```bash
HERMES_PLUGINS_DEBUG=1 hermes plugins list
```

Verbose discovery logs on stderr — scanned dirs, per-manifest key/name/kind/source/path, skip
reasons, what `register()` added, and full tracebacks on parse or registration failure. The same
logs always go to `~/.hermes/logs/agent.log` (failures WARNING, everything DEBUG when the var is
set). From inside the gateway, tail instead:

```bash
hermes logs --level WARNING | grep -i plugin
```

## Dependencies

Declare them in `pyproject.toml` next to `plugin.yaml` (preferred) or in the manifest's
`python_dependencies` list. `pyproject.toml` wins when both exist.

```toml
[project]
name = "my-plugin"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = ["somepkg>=1.0,<2"]
```

- **Always pin an upper bound.** Hermes' own 3-day `exclude-newer` release-age gate covers **Hermes'
  dependencies only** — your plugin's deps install under your policy, with no age check, inside
  Hermes' core constraints. A bare `>=X` adopts every future release unreviewed; the catalog review
  flags bare floors. Consider running your own `uv --exclude-newer "3 days"` in CI.
- Installation requests **consent**; enabling admits the candidate through PM with the union of
  core, extras and every profile's enabled plugins. Failure preserves the previous environment and
  selection. A declined or non-interactive dependency install leaves the plugin installed but
  disabled — `--enable` does **not** bypass dependency consent.
- `--no-deps` downloads without dependency consent and leaves the plugin disabled. `--yes-deps`
  answers up front for headless installs (mutually exclusive with `--no-deps`).
- `python_runtime: external` keeps a sidecar's deps out of the shared union; Hermes installs nothing.
- `security.allow_lazy_installs: false` blocks on-demand acquisition. For an SDK covered by a Hermes
  extra, use `pm.ensure_import("<extra>")` at the operation that needs it (never from a polled
  `check_fn`); `pm.available` is the read-only probe. Return the "restart required" error rather than
  importing from a second environment.
- Simply **declaring** `python_dependencies` never installs anything.

## Install-time security scanning

Every install and update runs a static scan (the Skills Hub threat engine) over the tree before
activation. Three verdicts: **safe** (installs), **caution** (findings shown; confirm, or pass
`--force`), **dangerous** (**blocked — `--force` does not override**). On update, a dangerous verdict
disables the plugin until you review and re-enable it.

Documentation prose (`README.md`, `AGENTS.md`, `docs/**/*.md`, `.txt`, `.rst`, `.html`) can never on
its own produce **dangerous**, and a whole-line comment or `CHANGELOG.md` scores as prose. Test trees
and fixtures are still scanned, but a quoted-only hostile string is a note and test code that would
execute on import is capped at caution. A provider plugin reading its **own** declared `requires_env`
key is not flagged. Disable with `plugins.scan_on_install: false`.

## Secrets

```python
from agent.secret_scope import get_secret

def check_requirements() -> bool:
    return bool(get_secret("MY_API_KEY"))
```

- **Never `os.getenv`.** One process serves many profiles and `os.environ` holds the launch
  profile's `.env`. Under multiplexing a bare read answers with the wrong profile's key.
- A registry-classified `UnscopedSecretError` at a boot-time probe is DEBUG, not a traceback.
- Never read `.env`, Hermes's `auth.json`, or another tool's credential store directly, and never
  refresh/rotate/write OAuth tokens. Route credential needs through Hermes (see
  `hermes-plugin-other-providers` for `credentialed_provider_request`).
- Settings go in `config.yaml`; **secrets go in `.env` only**.

## Storage, config and state

```python
from plugins.plugin_storage import plugin_data_dir, plugin_db

state_file = plugin_data_dir("my-plugin") / "state.json"   # <home>/plugin-data/<name>/
conn = plugin_db("my-plugin")                              # <data dir>/data.db (WAL)
```

- Never write runtime state into the plugin directory — that is the install tree, replaced by
  `update` and deleted by `remove`.
- `plugin_data_dir` resolves `get_hermes_home()` **on every call** so it follows the active profile —
  do not cache it across a profile switch.
- `plugin_db` opens WAL via the shared fallback helper (network filesystems degrade to DELETE
  safely) with `check_same_thread=False`; the caller owns transactions.
- Secrets do not belong here.

**Config vs state:** user-visible settings go through `ctx.get_config` / `ctx.set_config` (resolved
under `plugins.entries.<id>.settings` — global, cross-plugin and traversal keys are rejected);
plugin-owned cursors/caches/dedup data go in `ctx.state` (profile-scoped, atomically replaced,
safe across concurrent writers, 10 MiB per plugin). Neither API exposes another plugin's namespace.

```python
def register(ctx):
    endpoint = ctx.get_config("endpoint", default="https://example.invalid")
    cursor = ctx.state.get("cursor", default={"page": 0})
    ctx.state.set("cursor", {"page": cursor["page"] + 1})
```

`config_schema` in the manifest renders as a settings form on the plugin's own page under the
Desktop app's Settings → Plugins; saving writes through the same writer as `ctx.set_config()`.

## Thread-safe lazy singletons

Hermes runs multiple threads in one process, so the hand-rolled `global _client; if _client is None:
...` pattern is a TOCTOU race that leaks the loser's resource. Use the helpers:

```python
from plugins.plugin_utils import lazy_singleton, SingletonSlot

@lazy_singleton
def get_client():
    return ExpensiveClient(load_config())   # runs exactly once
client = get_client()
get_client.reset()                          # tests / teardown

_slot: SingletonSlot = SingletonSlot()
def get_client_with_cfg(config=None):
    return _slot.get(lambda: ExpensiveClient(resolve(config)))
```

Both serialize concurrent first calls with double-checked locking and run the factory at most once; a
raising factory caches nothing and the next call retries.

## Host isolation

`plugins.isolation: host` runs every **non-bundled** plugin in a per-profile host process
(`hermes_cli/plugin_host*.py`), talking over a framed JSON protocol. Plugins do not change: they get
the same `ctx`. What changes:

- **No shared interpreter** — a plugin cannot read another profile's memory or patch Hermes
  internals. Each profile gets its own host, started with only that profile's env and secrets.
- **Crashes stay contained** — a plugin crash kills its host, not Hermes; the in-flight call returns
  a tool error and Hermes restarts the host and reloads its plugins.
- **These surfaces need in-process code and are refused with a clear reason:** gateway platform
  adapters (`register_platform`), approval transports, Telegram/platform handlers, model-provider
  profiles that build their own SDK client (`create_client`), streaming dashboard endpoints,
  computer-use providers, and anything that monkeypatches Hermes modules.
- The refusal table is `plugin_isolation.HOST_UNSUPPORTED_CTX_METHODS`; the same table drives the
  static audit shown by `hermes plugins validate` / `show`.
- A host never spawns a host; memory / context-engine / cron providers and model-provider profiles
  route through it too.

**Locking it for a shared deployment:** `plugins.isolation` is ordinary profile config, so anyone
who can edit a profile can turn it off. Pin it in the **managed scope** (`/etc/hermes/config.yaml`),
which wins over every profile's config and which `hermes config set` refuses to change:

```yaml
plugins:
  isolation: host
  host:
    launcher: []      # optional argv prefix, e.g. a sandbox runner
```

`hermes plugins validate <dir>` and `hermes plugins show <name>` report whether a plugin runs in the
host and, if not, why.

## Testing

Put plugin tests in `tests/plugins/`. Rules (`tests/AGENTS.md`):

- **Always run the suite with `scripts/run_tests.sh`, never bare `pytest`.**
- **Load the plugin through the real discovery path** against a temporary `HERMES_HOME`; assert
  behaviour — "the tool is registered", "the hook fired with these kwargs" — never registry/catalog
  counts, source-reading, or "a global version literal changed".
- Tests must **never write to `~/.hermes/`**.
- Platform-specific behaviour is tested on that host with `@pytest.mark.platforms(...)`, never by
  faking `sys.platform`.
- Approval/security-boundary paths are E2E'd with real imports against a temp `HERMES_HOME`.

`hermes plugins doctor` is the fastest smoke test while iterating; it is not a substitute for the
pytest suite in a real contribution.

## Shipping checklist

1. `hermes plugins doctor . --ci` passes with no errors.
2. `hermes plugins validate /path/to/plugin --install-deps` passes (this is what catalog CI runs).
3. Every handler returns a JSON string and every hook callback takes `**kwargs`.
4. Credentials read via `get_secret`, never `os.getenv`; paths via `get_hermes_home`.
5. Python dependencies have upper bounds and are declared in `pyproject.toml` or the manifest.
6. `requires_hermes` is a truthful SemVer floor, never newer than the release you tested on.
7. Runtime state goes to `plugin_data_dir` / `plugin_db`, not the install tree.
8. Declared `provides_tools` / `provides_hooks` / `capabilities` match what `register()` actually does.
