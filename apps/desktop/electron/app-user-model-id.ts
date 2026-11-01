/**
 * The Windows AppUserModelID the process declares.
 *
 * The taskbar matches a running window to a shortcut or pin by this id. NSIS stamps the shortcuts it installs with the
 * build's `appId`, so a Light build must declare that same id. Declaring the stable `com.nousresearch.hermes` instead
 * groups the window under any other pin that owns it, such as a stale Hermes pin whose executable is gone, and the
 * taskbar then paints a blank icon for the running app.
 *
 * Other artifacts keep the stable id: the MSIX package identity of the bundled and store builds depends on it.
 */
export function appUserModelId(identity: { appId: string; light: boolean }, identityAppName: null | string): string {
  return identityAppName || identity.light ? identity.appId : 'com.nousresearch.hermes'
}
