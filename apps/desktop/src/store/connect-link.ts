import { atom } from 'nanostores'

import { translateNow } from '@/i18n'
import { type ConnectLink, parseConnectLink } from '@/lib/connect-link'

import { notify } from './notifications'

/**
 * The `hermes://connect` link awaiting the user's confirmation, or null. In memory only: the invitation token it
 * may carry is never persisted, and is dropped the moment the link is cancelled or its invitation redeemed.
 */
export const $connectLinkRequest = atom<ConnectLink | null>(null)

/** Validate a deep link's params into a pending request, or tell the user the link is not valid. */
export function requestConnectFromDeepLink(params: Record<string, string | undefined>): boolean {
  const link = parseConnectLink(params)

  if (!link) {
    notify({
      kind: 'error',
      title: translateNow('firstRun.connectLink.invalidTitle'),
      message: translateNow('firstRun.connectLink.invalidBody')
    })

    return false
  }

  $connectLinkRequest.set(link)

  return true
}

export function clearConnectLink(): void {
  $connectLinkRequest.set(null)
}
