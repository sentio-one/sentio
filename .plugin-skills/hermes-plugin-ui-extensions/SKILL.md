---
name: hermes-plugin-ui-extensions
description: "Extend the Hermes user interfaces: desktop app plugins (@hermes/plugin-sdk), Ink TUI widgets, and web dashboard plugins. Use when the user wants a desktop pane, status bar item, palette command, theme, TUI panel, or dashboard tab."
license: MIT
---

# Hermes UI Extensions

> Verified against source at commit `e5eb6b36` (2026-10-15). Static reading only.
> Sources: `website/docs/developer-guide/desktop-plugin-sdk.md`,
> `website/docs/user-guide/features/extending-the-dashboard.md`,
> `skills/autonomous-ai-agents/hermes-agent/references/tui-widgets.md`.

Three unrelated systems share the word "plugin". Pick by surface:

| Surface | Import / marker | Location | Build step |
|---|---|---|---|
| Native **desktop app** | `@hermes/plugin-sdk` (one ESM file) | `$HERMES_HOME/desktop-plugins/<id>/plugin.js` | none |
| **Ink TUI** widgets | the `sdk` argument | `~/.hermes/tui-widgets/<id>.mjs` | none |
| **Web dashboard** | `window.__HERMES_PLUGIN_SDK__` + `manifest.json` | `~/.hermes/plugins/<id>/dashboard/` | pre-built IIFE bundle |

Only the backend `plugin_api.py` namespace (`/api/plugins/<id>`) is shared between the desktop and
dashboard SDKs. Hermes' bundled `hermes-agent` skill carries its own shorter references for desktop
and TUI — this skill is the deeper one.

---

## Desktop app plugins

A desktop plugin is a single ESM file that default-exports a `HermesPlugin`. It imports **exactly
one** module and needs no build step.

```javascript
// ~/.hermes/desktop-plugins/hello/plugin.js   <- folder name MUST equal the id
import { host, haptic, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

function HelloPane() {
  const gateway = useValue(host.state.gateway)
  return jsxs('div', {
    className: 'flex h-full flex-col gap-2 p-3 text-sm',
    children: [
      jsx('div', { className: 'font-medium', children: 'Hello, Hermes' }),
      jsx('div', { className: 'text-(--ui-text-tertiary)', children: `gateway: ${gateway}` })
    ]
  })
}

export default {
  id: 'hello',            // must match the folder name
  name: 'Hello',
  // defaultEnabled: false,   // opt-in plugin, off until the user flips it on
  register(ctx) {
    ctx.register({
      id: 'pane', area: 'panes', title: 'hello',
      data: { placement: 'right', width: '260px' },
      render: () => jsx(HelloPane, {})
    })
  }
}
```

The app watches `desktop-plugins/` and loads the file within seconds, hot-reloading every later
save. Fall back to ⌘K → **Reload desktop plugins** if it does not appear; a load failure raises a
toast naming the error.

### Contract

```ts
interface HermesPlugin {
  id: string                 // stable slug; the `plugin:<id>` source and id namespace
  name?: string              // human name; defaults to id
  defaultEnabled?: boolean   // default true; false ships an opt-in plugin
  register: (ctx: PluginContext) => void
}

interface Contribution {
  id: string            // local id; the host namespaces it to `<pluginId>:<localId>`
  area: string          // WHERE it goes (an area constant)
  title?: string
  order?: number        // sort within the area (lower = earlier)
  when?: () => boolean  // dynamic visibility
  enabled?: boolean
  render?: () => ReactNode
  data?: unknown        // area-specific payload
}
```

`register(ctx)` is called once at load. `ctx` auto-tags provenance and namespaces every
contribution id, so two plugins can never collide.

```ts
interface PluginContext {
  readonly source: string                       // 'plugin:hello'
  register: (c: PluginContribution) => () => void       // returns a disposer
  registerMany: (cs: PluginContribution[]) => () => void
  registerSettingsPage: (page: PluginSettingsPage) => () => void
  rest: <T>(path: string, opts?: PluginRestOptions) => Promise<T>   // your /api/plugins/<id>
  socket: (path: string, onMessage: (data: unknown) => void) => () => void
  onEvent: (type: string, listener: (event: GatewayEvent) => void) => () => void
  onDispose: (fn: () => void) => void
  setTimeout / setInterval / addEventListener            // each returns a disposer
  os: PluginOs          // native notification, open-external, reveal-in-file-manager, clipboard
  pet: PluginPet        // lines in the core pet's speech bubble
  storage: PluginStorage // plugin-scoped JSON persistence (hermes.plugin.<id>.*)
  runAction(id) / listActions()
  i18n.register(...) / i18n.registerAppLocale(...)
}
```

### Contribution areas

| Surface | `area` | You provide |
|---|---|---|
| Layout pane | `PANES_AREA` (`'panes'`) | `title` + `render` + `data: { placement, dock?, width?, height? }` |
| Full page | `ROUTES_AREA` | `data: { path }` + `render` |
| Sidebar nav | `SIDEBAR_NAV_AREA` | `data: { path, label, codicon }` |
| Status bar | `STATUSBAR_AREAS.left` / `.right` | `render` (or `data` as `StatusbarItem`) |
| Title bar | `TITLEBAR_AREAS.left`/`.center`/`.right` | `data` as `TitlebarTool`, or mount-scoped `<Contribute>` |
| Page header | `WORKSPACE_PAGE_HEADER_AREA` | `<WorkspacePageHeaderControl>` inside your page |
| ⌘K palette | `PALETTE_AREA` | `data: PaletteContribution` |
| Keybind | `KEYBINDS_AREA` | `data: KeybindContribution` |
| Theme | `THEMES_AREA` | `data` as a `DesktopTheme` |
| Composer | `COMPOSER_AREAS.*` | render slots, or middleware / attachment providers |
| Model menu rows | `MODEL_MENU_ROW_AREA` | `data: ModelMenuRowContribution` — leading icon / trailing badge |
| Session rows | `SESSION_ROW_AREAS.leading`/`.trailing` | `data: { render({ sessionId }) }` |
| Appearance settings | `APPEARANCE_AREAS.extra` | `render` — appended to Settings → Appearance |
| Plugin settings page | `SETTINGS_PLUGINS_AREA` | use `ctx.registerSettingsPage({ id, title, render, icon?, order?, children? })` |
| Transcript directive | `TRANSCRIPT_DIRECTIVE_AREA` | `data: { name, render({ attrs, streaming }) }` |

**Panes.** `placement` is `'main' | 'left' | 'right' | 'top' | 'bottom'` — the pane stacks as tabs
with panes of that role; the user can drag it afterward. To land on a specific edge instead, add
`dock: { pane, pos }` (the same gesture as dragging onto a drop chip): `pane` is any pane id
(`workspace` is the main thread; also `sessions`, `terminal`, `files`, `review`, `logs`), `pos` is
`'top'|'bottom'|'left'|'right'|'center'`. Declare `width`/`height` so the pane does not claim half
the zone. Closing the only pane a plugin contributed disables that plugin.

**Themes.** Registering a theme only *lists* it in the picker; it does not select it. Switch with
`useTheme().setTheme(name)` from a component, or `requestTheme(name)` from a callback with no
component (a gateway event, a socket handler). `requestTheme` returns `false` for an unresolvable
name and leaves the appearance alone — use it as the availability check. To tint the active theme
rather than replace it, use `setAccentOverride(hex)` and clear it in `ctx.onDispose`.

**Transcript directives** let the model render your component inline by emitting
`::name{key="value"}` as a whole paragraph. Attributes are **untrusted model output** — validate
them. Unclaimed or malformed directives fall back to plain text. After registering one, tell the
model it exists (a skill or user instruction); it will not discover the name.

### Host API highlights

```ts
host.state.*          // readonly atoms: activeSessionId, busy, awaitingResponse, busyBySession,
                      // focusedSessionId, focusedStoredSessionId, focusedSessionProfile,
                      // focusedUsage, cwd, gateway (socket state, NOT turn-busy), model, profile, viewport
host.request(method, params)      // the app's own gateway JSON-RPC — the real power
host.requestProfile(route, method, params, timeoutMs?, { spawnPriority })
host.onEvent(type, fn)            // gateway event stream ('*' = all)
host.notify({ kind, message })    // in-app toast
host.navigate('/route')
host.openSession(id, { profile?, intent? }) / host.newChat(profile?)
host.openWorkspace(id, { render, title?, onClose? })
host.composer.getDraft/setDraft/insertText/submit/focus
host.sessions.pin/reorder/reorderPinned/setColor
host.settings.get/set/subscribe          // closed allowlist of Desktop-local appearance keys
host.skills / host.toolsets / host.profiles / host.pluginDecisions  // typed capabilities bridge
ctx.os.notify({ title, body?, icon?, activate?, onActivate?, actions? })  // native OS notification
ctx.pet.say(text, { id?, tone?, ttlMs? })
ctx.runAction('view.showBrowser')         // run a built-in app action by id
```

Disable composer/plugin actions from the **focused session's** turn-busy
(`host.state.busyBySession[sessionId]`), never from `gateway`.

Use the app's React Query client (`useQuery` / `queryClient`) instead of hand-rolled fetch loops;
poll no faster than a few seconds and prefer `host.onEvent` / `ctx.socket`.

### Rules and pitfalls

- **The only importable specifiers are `@hermes/plugin-sdk`, `react` and `react/jsx-runtime`.**
  Anything else fails to resolve, on purpose.
- **No JSX in a disk plugin** — the file loads uncompiled. Write `jsx()` / `jsxs()` calls or
  `React.createElement`. (Bundled plugins are Vite-built, so JSX is fine there.)
- **Never hardcode colors.** Panes already sit on the app's editor background; use theme variables
  (`var(--ui-text-secondary)`, `var(--ui-text-tertiary)`, `var(--ui-stroke-secondary)`,
  `var(--ui-accent)`). For canvas, resolve once with `getComputedStyle(canvas).getPropertyValue(...)`.
- **Canvas panes must track their container** with a `ResizeObserver` and resize the canvas
  width/height attributes, not just CSS.
- **Bare globals are not tracked** — `window.setInterval`, `window.addEventListener`, an appended
  `<style>` survive disable and hot-reload. Use `ctx.setTimeout` / `ctx.setInterval` /
  `ctx.addEventListener` or `ctx.onDispose`.
- **Read state imperatively** (`$atom.get()`) in handlers, never from a render closure; subscribe
  (`useValue`) only in the leaf that renders the value.
- **Module evaluation has a 10 s deadline** — do waiting inside `register()`, not at top level.
- **One id, one file.** Two folders exporting the same `id` load first-wins; the later shows
  `duplicate id`.
- **`ctx.socket` is a no-op on OAuth remotes** — always keep a polling fallback.

### Security model (read this before shipping)

A loaded plugin is evaluated as ESM in the renderer realm with **full app authority** — the React
singleton, the whole SDK, and `window.hermesDesktop` (files, git, terminal, installs). The loader
provides **error isolation only**: a plugin cannot crash the app, but it can do anything the app can.
Storage namespaces are a convention, not a wall.

This is acceptable for local files you wrote. For catalog installs, trust comes from admission, backed
by two tripwires: the `desktop surface` lint and the loader's import allowlist. Neither is a sandbox.
Catalog rule 8 forbids prototype patching, `eval`/`new Function`, importing anything but
`@hermes/plugin-sdk`/`react`, script-tag injection, and reaching into the app's internal stores or
markup (`data-slot`/`data-tour`/`data-testid` queries, a `document.body` MutationObserver).

### Unified package — one folder, both halves

```
~/.hermes/plugins/<id>/           # ONE installable folder
├── plugin.yaml                   # agent half
├── skills/
├── dashboard/{manifest.json,plugin_api.py}
└── desktop/plugin.js             # desktop half
```

Electron copies `desktop/plugin.js` into `$HERMES_HOME/desktop-plugins/<id>/` beside a
`.hermes-package.json` marker and loads **the copy** — so editing the package in place changes
nothing on screen until a Rescan, an install/update, or a restart. Develop from a symlinked
checkout instead (`ln -s ~/src/my-plugin ~/.hermes/plugins/my-plugin`), which re-syncs on every
save. `hermes plugins doctor <path>` warns when the copy is stale.

The desktop half is **opt-in** (inventories in Capabilities → Plugins, off until toggled), matching
the Python half's `plugins.enabled` gate. Tell the user to flip it on before debugging "plugin not
appearing".

---

## Ink TUI widgets

A widget is a plain `.mjs` file at `~/.hermes/tui-widgets/<id>.mjs` that default-exports
`register(sdk)`. The TUI watches the directory (hot-load in ~1s; `/widgets-reload` forces a rescan)
and the widget id becomes its slash command automatically — no other registration exists.

```js
export default function register(sdk) {
  const { Box, Text, defineWidgetApp, h } = sdk

  defineWidgetApp({
    id: 'clock',                    // slash command name
    help: 'live clock in the dock', // `/` completion metadata
    mode: 'ambient',                // 'ambient' docks; 'modal' (default) takes input
    init: arg => ({ label: arg.trim() || 'UTC' }),          // null = print usage
    reduce: (state, { ch, key }) => (key.escape || ch === 'q' ? null : state),
    render: ({ state, t }) => h(sdk.Dialog, { width: 24 },
      h(Text, { color: t.color.label }, state.label))
  })
}
```

- `sdk` provides `defineWidgetApp`, `openWidget`, `updateWidget`, `isCtrl`, `React`, `h`
  (createElement — **no JSX in .mjs**), components `Box`, `Text`, `Dialog`, `Overlay`,
  `WidgetGrid`, `GridAreas`, `Accordion`, loaders `Shimmer`/`ShimmerRows`/`useShimmerPhase`,
  chart helpers `sparkline`, `sparkRows`, `gauge`, `hbars`, and theme tones on `t.color.*`.
- **`mode: 'ambient'`** captures no input (the command toggles it) and `render` returns a card
  (`Dialog`), never `Overlay`. **`mode: 'modal'`** owns every keypress: `reduce` returns the next
  state, the same reference to swallow a key, or `null` to close; `render` wraps content in
  `Overlay`.
- Placement via `zone` — every zone **reserves real space**, nothing paints over the transcript.
  Docks: `dock-top`, `dock-bottom` (default). Rails: `top-left`, `top-right`, `bottom-left`,
  `bottom-right`; set `width` on the app to the card width (default 44) — the rail reserves exactly
  that many columns.
- **Cards must never resize while ticking**: give `Dialog` an explicit `width`, pad dynamic numbers
  (`String(v).padStart(6)`), keep row counts constant per phase.
- Async data: fire the fetch in `init`, land results with `sdk.updateWidget(app, fn)` (a no-op if
  the widget was closed). Animation: own a timer in a component via `React.useState` +
  `React.useEffect`, intervals ≥ 250 ms.
- Always use theme tones (`t.color.*`), never hardcoded hexes.
- Auto-open (only when asked): end `register(sdk)` with `sdk.openWidget(app, app.init(''))` — it
  re-docks on every `/widgets-reload`.
- A thrown `register()` is logged and skipped; check `~/.hermes/logs/tui_gateway_crash.log`.

---

## Web dashboard plugins

A dashboard plugin is a `dashboard/` subfolder inside a normal plugin directory:

```
~/.hermes/plugins/my-plugin/
├── plugin.yaml              # optional agent half
├── __init__.py
└── dashboard/
    ├── manifest.json        # required
    ├── dist/index.js        # required — pre-built IIFE bundle
    ├── dist/style.css       # optional
    └── plugin_api.py        # optional FastAPI routes
```

```json
{
  "name": "my-plugin",
  "label": "My Plugin",
  "icon": "Sparkles",
  "version": "1.0.0",
  "tab": { "path": "/my-plugin", "position": "after:skills", "override": "/", "hidden": false },
  "slots": ["sidebar", "header-left"],
  "entry": "dist/index.js",
  "css": "dist/style.css",
  "api": "plugin_api.py"
}
```

Required fields: `name`, `label`, `entry` (defaults `dist/index.js`). `tab.path` is required.
`tab.position` is `"end"` (default), `"after:<path-segment>"`, or `"before:<path-segment>"`.
`tab.override` (a built-in route like `"/"`) **replaces** that page instead of adding a tab — only
one plugin may override a given path. `tab.hidden: true` registers without a nav tab (slot-only
plugins). `icon` is a Lucide name; unknown names fall back to `Puzzle`. `slots` is documentation
only — real registration happens in the bundle via `registerSlot()`.

```javascript
// dashboard/dist/index.js  — plain IIFE, no build step required
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  const { React } = SDK;
  const { Card, CardHeader, CardTitle, CardContent } = SDK.components;

  function MyPage() {
    return React.createElement(Card, null,
      React.createElement(CardHeader, null, React.createElement(CardTitle, null, "My Plugin")),
      React.createElement(CardContent, null,
        React.createElement("p", { className: "text-sm text-muted-foreground" },
          "Hello from my custom dashboard tab.")));
  }

  window.__HERMES_PLUGINS__.register("my-plugin", MyPage);   // name MUST equal manifest name
})();
```

- **Never import React** — it comes from `SDK.React`. `SDK` also exposes `hooks.*`, shadcn
  components (`Card*`, `Badge`, `Button`, `Input`, `Select*`, `Tabs*`, `PluginSlot`, …), `api`
  (typed client), `fetchJSON`, `utils.cn`, `utils.timeAgo`, `useI18n`.
- **Shell slots:** `window.__HERMES_PLUGINS__.registerSlot(name, slot, Component)`. Slots include
  `backdrop`, `header-left`, `header-right`, `header-banner`, `sidebar` (only under
  `layoutVariant: "cockpit"`), `pre-main`, `post-main`, `footer-left`, `footer-right`, `overlay`.
  **Page-scoped slots** render only on a built-in page: `<page>:top` / `<page>:bottom` for
  `sessions`, `analytics`, `logs`, `cron`, `skills`, `config`, `env`, `docs`, `chat`.
- Re-registering the same `(plugin, slot)` replaces the earlier one.
- Plugins have **2 seconds** after their script loads to call `register()`; a later registration
  still appears (the nav is reactive).
- Discovery scans `~/.hermes/plugins/<name>/dashboard/` (user, wins on conflict), the bundled
  `plugins/` and `plugins/memory/`, and `./.hermes/plugins/` (project, needs
  `HERMES_ENABLE_PROJECT_PLUGINS`). Rescan with
  `curl http://127.0.0.1:9119/api/dashboard/plugins/rescan`.

### Backend routes

```python
# dashboard/plugin_api.py
from fastapi import APIRouter

router = APIRouter()

@router.get("/board")
async def board():
    return {"items": ["one", "two", "three"]}
```

Mounts under `/api/plugins/<id>/` (so `GET /api/plugins/my-plugin/board`). The module must export a
module-level `router = APIRouter()`. Routes mount **at startup only** — restart the gateway after
adding them. Backend code runs in the gateway process and can import Hermes internals
(`hermes_state`, `hermes_cli.config`, …).

The Python backend is **gated separately**: it is imported only when the plugin is in
`plugins.enabled` (and not `plugins.disabled`). Project plugins never auto-import Python — a
security boundary, not an oversight.

The desktop half reaches it with `ctx.rest('/board')` / `ctx.socket('/events', fn)`. The
dashboard half uses `SDK.fetchJSON("/api/plugins/my-plugin/board")`.

Push events to a desktop half from the backend:

```python
from hermes_cli.plugin_events import broadcast_plugin_event
broadcast_plugin_event("my-plugin", "feed.updated", {"count": 3})
# -> event "plugin.my-plugin.feed.updated" reaches every connected desktop client
```

Wire name is always `plugin.<plugin_id>.<event>`; `plugin_id` is the catalog name
(`[a-z0-9_-]{1,64}`, no dots) and `event` is the bare dotted name. It reaches clients in the process
the Desktop is connected to; from `hermes chat`, cron, or `hermes plugins validate` it is a logged
no-op.

## Verifying

- Desktop: the plugin appears after **Reload desktop plugins**; no error toast. For a unified
  package, confirm the desktop half is toggled on.
- TUI: `/widgets-reload` lists the file under `loaded:`; `/<id>` docks it.
- Dashboard: `curl http://127.0.0.1:9119/api/dashboard/plugins` includes your manifest; check
  devtools console for `window.__HERMES_PLUGINS__ is undefined` (SDK did not initialise).
