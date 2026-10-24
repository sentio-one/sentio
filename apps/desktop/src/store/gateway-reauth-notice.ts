import { translateNow } from '@/i18n'
import { failDesktopBoot } from '@/store/boot'
import { dismissNotification, type NotificationAction, notify, RECOVERY_ACTIONS } from '@/store/notifications'

const noticeId = (scope: string): string => `gateway-reauth:${scope}`

/**
 * A secondary (per-profile) socket whose remote signed the user out. Same message the primary connection gives, but it
 * stays until the socket opens again: a short toast was missed, and the status bar then only said "offline" while the
 * reconnect ladder kept retrying. On the main connection the button opens the sign-in flow; on another gateway it opens
 * Gateways, where sign-in is per connection. The id dedupes repeated failures into one notice.
 */
export function notifySecondaryReauth(scope: string, error: Error, onMainConnection: boolean): void {
  const action: NotificationAction = onMainConnection
    ? { label: translateNow('boot.errors.signInAgain'), onClick: () => failDesktopBoot(error.message) }
    : RECOVERY_ACTIONS.openGateways()

  notify({
    action,
    detail: error.message,
    durationMs: 0,
    id: noticeId(scope),
    kind: 'error',
    message: translateNow('boot.errors.gatewaySignInRequiredDetail'),
    title: translateNow('boot.errors.gatewaySignInRequired')
  })
}

export function dismissSecondaryReauthNotice(scope: string): void {
  dismissNotification(noticeId(scope))
}
