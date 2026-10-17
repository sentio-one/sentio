import { atom } from 'nanostores'

import { type ConnectLink, parseConnectLink } from '@/lib/connect-link'


/**
 * The `hermes://connect` link awaiting the user's confirmation, or null. In memory only: the invitation token it
 * may carry is never persisted, and is dropped the moment the link is cancelled or its invitation redeemed.
 */
export const $connectLinkRequest = atom<ConnectLink | null>(null)

/**
 * A link that arrived but is not valid. Shown by the dialog itself: a toast would sit behind the first-run
 * overlay of a remote-only build, so the user would see nothing happen.
 */
export const $connectLinkInvalid = atom(false)

/** Validate a deep link's params into a pending request, or tell the user the link is not valid. */
export function requestConnectFromDeepLink(params: Record<string, string | undefined>): boolean {
  const link = parseConnectLink(params)

  $connectLinkRequest.set(link)
  $connectLinkInvalid.set(!link)

  if (!link) {
    return false
  }

  return true
}

export function clearConnectLink(): void {
  $connectLinkRequest.set(null)
  $connectLinkInvalid.set(false)
}
