/**
 * First-run policy of the remote-only ("Light") artifact.
 *
 * Light carries no agent runtime, so its first launch must never offer the local install: it goes
 * straight to the remote-connection form, optionally preset with the company gateway URL that
 * `apps/desktop/light-defaults.json` bakes in at build time (scripts/bundle-electron-main.mjs).
 * Every other artifact keeps the local-or-remote choice, and ignores the preset.
 */
import type { InstallStamp } from './install-stamp'

declare const __HERMES_LIGHT_GATEWAY_URL__: string

/** The gateway URL baked into this artifact; '' on dev bundles and when none was configured. */
export const BAKED_GATEWAY_URL: string =
  typeof __HERMES_LIGHT_GATEWAY_URL__ === 'undefined' ? '' : __HERMES_LIGHT_GATEWAY_URL__

type StampLike = Pick<InstallStamp, 'payload'> | null | undefined

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]'])

/**
 * A gateway URL safe to preset: https (plain http only for loopback development), no credentials,
 * query or fragment, no trailing slash. Anything else is dropped, so a bad build input shows an
 * empty form instead of a wrong or secret-bearing one.
 */
export function normalizeGatewayUrl(raw: string): string {
  let url: URL

  try {
    url = new URL(raw.trim())
  } catch {
    return ''
  }

  const secure = url.protocol === 'https:' || (url.protocol === 'http:' && LOOPBACK_HOSTS.has(url.hostname))

  if (!secure || url.username || url.password || url.search || url.hash) {
    return ''
  }

  return url.origin + url.pathname.replace(/\/+$/, '')
}

export function isLightArtifact(stamp: StampLike): boolean {
  return stamp?.payload === 'light'
}

export function firstRunPolicy(
  stamp: StampLike,
  gatewayUrl: string = BAKED_GATEWAY_URL
): { remoteOnly: boolean; defaultGatewayUrl: string } {
  const remoteOnly = isLightArtifact(stamp)

  return { remoteOnly, defaultGatewayUrl: remoteOnly ? normalizeGatewayUrl(gatewayUrl) : '' }
}

/** False for Light: there is no runtime to install, whatever the renderer asks for. */
export function mayStartLocalBootstrap(stamp: StampLike): boolean {
  return !isLightArtifact(stamp)
}
