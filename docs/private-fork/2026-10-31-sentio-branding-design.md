# Rebrand what people see: Hermes to Sentio One

Status: implemented on 2026-10-31. Focused UI/Electron tests, desktop typecheck, the Light production build and `SentioOne-0.3.0-win-x64.exe` packaging passed. Launching the unpacked app with protocol registration disabled showed the confirmation dialog for both `sentio://connect` and legacy `hermes://connect` inputs. OS-handler and installed-app verification remain pending while the existing Hermes Light app is running with the legacy `hermes://` handler; its registry entry was left unchanged.

## Goal

Everything a person reads in the desktop app, its sign-in pages and its installer says **Sentio One**, not Hermes. That includes the big
**HERMES AGENT** lettering on an empty chat, which becomes **SENTIO ONE**.

## Scope (decided)

**In:** user-visible text only: translated UI strings in every language, the empty-chat wordmark, the window title, the app and
installer name, the web dashboard, and the sign-in and OAuth result pages the backend serves.

**Also in (added 2026-10-31):** the deep-link scheme. Links the app and backend generate use `sentio://` (section 6).

**Out, on purpose:** internal identifiers. The app ID `com.nousresearch.hermes-light`, `HERMES_HOME` and every other env var, IPC
channel names such as `hermes:openExternal`, `localStorage` keys, imports, code, comments, URLs, and Nous Research's model names
("Hermes" is also a model family).

Why not everything: about 15,400 occurrences of "Hermes" sit in `apps/desktop` alone, most of them identifiers. Rewriting them
conflicts with upstream on most files at every upstream merge and risks breaking working code.

## Design

### 1. Translated text, all languages: rebrand at the catalog

`apps/desktop/src/i18n/registry.ts` `resolveTranslations(locale)` builds the one merged catalog behind the `t` object (bundled
catalog, plugin overrides, backend language packs), memoized per locale. A rebrand step there walks the merged tree once per
locale: strings are rewritten, and function-valued messages (for example `t.intro.custom(personality)`) are wrapped so what they
return is rewritten too. `translateFrom` in `runtime.ts`, which resolves plugin bundles by key, applies the same rewrite.

Because upstream's translation files are not edited, upstream merges do not conflict and new upstream strings are rebranded
automatically.

Rewrite rules, in order (a new module `apps/desktop/src/i18n/brand.ts` owns them and the exclusions):

1. `Hermes Desktop`, `Hermes Agent`, `Hermes Light` become `Sentio One`.
2. A standalone `Hermes` becomes `Sentio One`.
3. Left alone: anything inside a URL, a code span, or an identifier-shaped token (`hermes://`, `HERMES_HOME`, `hermes-agent`);
   Nous model names (`Hermes 3`, `Hermes 4`, `Nous-Hermes…`, `Hermes-3-Llama…`); `Nous Research` and `Nous Portal` text.
4. Non-Latin scripts are untouched by the pattern (it matches the Latin word only). Each language gets a spot check, since
   possessives and grammar around a replaced word can read oddly.

### 2. The empty-chat wordmark

`apps/desktop/src/components/chat/intro.tsx` line 149: `const WORDMARK = 'HERMES AGENT'` becomes `'SENTIO ONE'`, still rendered by
`Wordmark` in Collapse Bold (the choice made in the visual review: option A, same face, new name). No new font is added. The
package that ships Collapse (`@nous-research/ui`) is MIT-licensed; the font files carry no separate licence file, so their own
terms are unconfirmed (recorded as low risk because the fork already ships them).

### 3. Hardcoded visible text

Direct edits to the visible literals outside the translation files: about 23 in the renderer, the window title in
`apps/desktop/index.html` (`<title>Hermes</title>`), the Electron main process's visible strings, the dashboard under `web/src`
(sign-in components such as `AuthWidget.tsx` and `OAuthLoginModal.tsx` and their translations), and the backend pages in
`hermes_cli/web_routers/` ("Return to Hermes and try again", "Authorization received...").

### 4. App name and installer

`apps/desktop/product-identity.cjs` is the single source of name-shaped values. For the Light variant:

- `display` changes from `Hermes Light` to `Sentio One`. This renames the window, the Start menu entry and `Sentio One.exe`.
- `pascal` changes from `HermesLight` to `SentioOne`, so the installer is `SentioOne-<version>-win-x64.exe`. Everything that
  names the old file is updated together: the `HermesLight-*` globs in `.github/workflows/release-light.yml`,
  `scripts/fork/refresh-main-release.sh`, `tests/scripts/test_refresh_main_release.py`, and the install notes text.
- Unchanged: `kebab` (`hermes-light`), `appId` (`com.nousresearch.hermes-light`) and `cliName`, preserving the CLI and app ID.
  Deep-link schemes are handled separately below.

**Consequence, accepted:** Electron keeps each user's local data (saved gateway connections and settings) in a folder named after
the app, so anyone already on `0.3.0` starts clean after the rename and re-enters their gateway URL. No testers have been
onboarded yet, so this is the cheapest moment.

### 5. Deep links: `sentio://`

Invite links and every other link that must open the app switch from `hermes://` to `sentio://`. The domain `sentiolangley.com` is
not involved: a custom scheme needs no domain, and `https` landing links (which would use it) are deferred to the website work.

- **Register both with the OS (decided 2026-10-31: `hermes://` links must keep working).** `apps/desktop/electron/main.ts` registers
  `sentio` and `hermes` (dev: `sentio-dev` and `hermes-dev`): `registerDeepLinkProtocol` is called once per scheme, and
  `electron-builder.config.cjs` `protocols[].schemes` becomes `['sentio', 'hermes']`. Invites already sent, and vendors' "Add to
  Hermes" buttons (`hermes://mcp/install`), keep opening this app from a browser.
- **Parse both.** `DEEPLINK_SCHEMES` accepts `sentio` and `hermes` (dev adds `sentio-dev` and `hermes-dev`), and the renderer parsers
  (`lib/connect-link.ts`, `lib/hermes-open-target.ts`, `lib/mcp-deeplink.ts`) accept both prefixes, still rejecting lookalike hosts
  and credentials.
- **Generate `sentio://`.** `hermes_cli/web_routers/access_invites.py` (invite links), `tools/connectors/gateway/wire.py` (the
  `connections/done` link the browser leg returns to), and the app's own links (`store/native-notifications.ts`, plugin and MCP
  install links) mint `sentio://`. The implementation plan enumerates every generator from a search for `hermes://`.
- **Accepted cost: the `hermes://` handler is shared with Nous's Hermes Desktop.** Each launch rewrites the OS handler for the schemes
  an app registers, so on a machine with both apps installed, whichever launched last owns `hermes://`. `sentio://` has no such
  conflict, which is why new links use it. Testers are unlikely to have both apps installed.
- **Effect on existing installs:** a `0.3.0` install registers only `hermes://`, so it cannot open `sentio://` links until it
  updates; `hermes://` links open in both old and new versions.
- **Docs:** `docs/private-fork/INVITES_DESIGN.md` and `FORK_DECISIONS.md` mention the old scheme and are updated.
- **Verify on Windows:** build the installer, install it, and confirm the handler is registered under
  `HKCU\Software\Classes\sentio` and `HKCU\Software\Classes\hermes`, and that both a `sentio://connect?...` and a `hermes://connect?...` link open the confirmation dialog.

### 6. Testing

- A behaviour test for `brand.ts`: strings and function messages are rebranded; URLs, env vars, `hermes://`, and model names are
  not; the result is stable across locales.
- A test that every resolved English string and every locale's catalog contains no standalone, non-excluded "Hermes" (a guard so
  upstream merges that add strings cannot silently reintroduce it).
- `product-identity` tests updated for the new display name and artifact name.
- Deep-link tests: the main process accepts `sentio` and `hermes` and rejects other schemes; the connect-link and open-target
  parsers accept both prefixes and still reject lookalike hosts and credentials; the invite minted by the backend starts with
  `sentio://connect?`; the registrar is called for both `sentio` and `hermes`.
- Build the installer and open the app: empty chat, boot screen, settings, sign-in and an OAuth result page.

## Open

- **The agent still names itself Hermes.** `agent/prompt_builder.py` `DEFAULT_AGENT_IDENTITY` begins "You are Hermes Agent, built by
  Nous Research." so the assistant introduces itself that way in chat. Changing it alters model behaviour, so it is a separate piece
  of work, not part of this one.
- **Visible text I have not found yet** goes on a short list for review after the first build; plugin-provided and backend-generated
  messages are the likely sources.
- Fonts: Collapse's own licence terms are unconfirmed (see 2).
