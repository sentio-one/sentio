/**
 * `hermes://connect?url=<gateway>&invite=<token>`: a company admin's link that points the app at their gateway
 * and, optionally, carries a single-use invitation to a profile on it.
 *
 * The link is attacker-controllable input (any web page can open it), so it is parsed strictly, only ever
 * shown to the user for confirmation, and never acted on by itself. Unknown parameters are ignored.
 */

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]'])
const INVITE_RE = /^[A-Za-z0-9_-]{20,200}$/
const MAX_URL_LENGTH = 2048

export interface ConnectLink {
  /** Normalised gateway base URL (https, or http for loopback), no trailing slash. */
  url: string
  /** What the user is asked to confirm: the parsed host (punycode for lookalikes) plus any port. */
  host: string
  /** The invitation token, or null. Held in memory only; dropped after redeem or cancel. */
  invite: string | null
}

/** A safe gateway URL, or '' (https; plain http only for loopback; no credentials, query or fragment). */
export function normalizeGatewayUrl(raw: string): string {
  if (typeof raw !== 'string' || raw.length > MAX_URL_LENGTH) {
    return ''
  }

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

export function parseConnectLink(params: Record<string, string | undefined>): ConnectLink | null {
  const url = normalizeGatewayUrl(params.url ?? '')

  if (!url) {
    return null
  }

  const invite = params.invite

  if (invite !== undefined && !INVITE_RE.test(invite)) {
    return null
  }

  return { url, host: new URL(url).host, invite: invite ?? null }
}

export type RedeemOutcome =
  | { ok: true; profile: string }
  | { ok: false; reason: 'invalid' | 'rate_limited' | 'not_supported' | 'unreachable' }

type ApiCall = (request: { method: string; path: string; body: unknown }) => Promise<unknown>

interface RedeemOptions {
  attempts?: number
  sleep?: (ms: number) => Promise<void>
}

// The gateway's own answers, matched on its error codes rather than on transport wording.
const FINAL_REASONS: Array<[RegExp, Exclude<RedeemOutcome, { ok: true }>['reason']]> = [
  [/invalid_invite/, 'invalid'],
  [/too_many_attempts|\b429\b/, 'rate_limited'],
  [/access_policy_not_configured|\b409\b/, 'not_supported']
]

/**
 * Redeem an invitation on the gateway the app is now connected to. Right after "Apply and reconnect" the new
 * connection may still be settling, so transport and sign-in errors are retried; the gateway's own refusals are final.
 */
export async function redeemInvite(api: ApiCall, token: string, options: RedeemOptions = {}): Promise<RedeemOutcome> {
  const { attempts = 6, sleep = ms => new Promise<void>(resolve => setTimeout(resolve, ms)) } = options

  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const result = (await api({ method: 'POST', path: '/api/access/invites/redeem', body: { token } })) as {
        profile?: unknown
      } | null

      return typeof result?.profile === 'string' && result.profile
        ? { ok: true, profile: result.profile }
        : { ok: false, reason: 'invalid' }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      const final = FINAL_REASONS.find(([pattern]) => pattern.test(message))

      if (final) {
        return { ok: false, reason: final[1] }
      }

      if (attempt < attempts) {
        await sleep(Math.min(500 * attempt, 3000))
      }
    }
  }

  return { ok: false, reason: 'unreachable' }
}
