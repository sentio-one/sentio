// A signed-out remote must tell the user so and offer the sign-in, on a secondary profile socket too (private fork,
// found on the first invited session: the app only said "Backend offline" and kept reconnecting).
import { beforeEach, expect, test, vi } from 'vitest'

const boot = vi.hoisted(() => ({ failDesktopBoot: vi.fn() }))

vi.mock('@/store/boot', () => boot)

import { en } from '@/i18n/en'

import { dismissSecondaryReauthNotice, notifySecondaryReauth } from './gateway-reauth-notice'
import { $notifications, clearNotifications } from './notifications'

beforeEach(() => {
  clearNotifications()
  boot.failDesktopBoot.mockClear()
})

test('a signed-out secondary on the main connection shows one lasting notice with a Sign in again button', () => {
  notifySecondaryReauth('sentio-sales', new Error('session expired'), true)
  notifySecondaryReauth('sentio-sales', new Error('session expired'), true)

  const shown = $notifications.get()
  expect(shown).toHaveLength(1)
  expect(shown[0].title).toBe(en.boot.errors.gatewaySignInRequired)
  expect(shown[0].message).toBe(en.boot.errors.gatewaySignInRequiredDetail)
  expect(shown[0].action?.label).toBe(en.boot.errors.signInAgain)

  shown[0].action?.onClick()
  expect(boot.failDesktopBoot).toHaveBeenCalledWith('session expired')
})

test('a secondary on another gateway sends the user to Gateways, which signs in per connection', () => {
  notifySecondaryReauth('other:alice', new Error('expired'), false)

  expect($notifications.get()[0].action?.label).toBe(en.notifications.actions.openGateways)
  expect(boot.failDesktopBoot).not.toHaveBeenCalled()
})

test('the notice lasts until the socket opens again', () => {
  vi.useFakeTimers()
  notifySecondaryReauth('sentio-sales', new Error('x'), true)
  vi.advanceTimersByTime(10 * 60_000)
  expect($notifications.get()).toHaveLength(1)

  dismissSecondaryReauthNotice('sentio-sales')
  expect($notifications.get()).toHaveLength(0)
  vi.useRealTimers()
})
