// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $connectLinkRequest, requestConnectFromDeepLink } from '@/store/connect-link'
import { $notifications } from '@/store/notifications'

import { ConnectLinkDialog } from './connect-link-dialog'

const TOKEN = 'B'.repeat(43)
const GATEWAY = 'https://gateway.acme.example'

function installDesktop() {
  const desktop = {
    probeConnectionConfig: vi.fn().mockResolvedValue({
      authMode: 'token',
      baseUrl: GATEWAY,
      error: null,
      providers: [],
      reachable: true,
      version: '1.0.0'
    }),
    testConnectionConfig: vi.fn().mockResolvedValue({ baseUrl: GATEWAY, ok: true, version: '1.0.0' }),
    applyConnectionConfig: vi.fn().mockResolvedValue({ mode: 'remote' }),
    oauthLoginConnectionConfig: vi.fn(),
    api: vi.fn().mockResolvedValue({ profile: 'alpha' })
  }

  Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: desktop })

  return desktop
}

async function continueAndApply(desktop: ReturnType<typeof installDesktop>) {
  fireEvent.click(await screen.findByText('Continue'))
  await screen.findByText('Gateway URL')
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 550))
  })
  fireEvent.change(await screen.findByPlaceholderText('Paste session token'), { target: { value: 'session-secret' } })
  fireEvent.click(screen.getByText('Test connection'))
  await waitFor(() => expect(desktop.testConnectionConfig).toHaveBeenCalled())
  await screen.findByText(/Connected to/)
  fireEvent.click(screen.getByText('Apply and reconnect'))
}

function receive(params: Record<string, string>) {
  act(() => {
    requestConnectFromDeepLink(params)
  })
}

beforeEach(() => {
  $connectLinkRequest.set(null)
  $notifications.set([])
})

afterEach(() => {
  cleanup()
  Reflect.deleteProperty(window, 'hermesDesktop')
  $connectLinkRequest.set(null)
})

describe('ConnectLinkDialog', () => {
  it('renders nothing until a link arrives', () => {
    installDesktop()
    const { container } = render(<ConnectLinkDialog />)

    expect(container.textContent).toBe('')
  })

  it('shows the server the link points at and contacts nothing until the user continues', async () => {
    const desktop = installDesktop()

    render(<ConnectLinkDialog />)
    receive({ url: GATEWAY, invite: TOKEN })

    expect(await screen.findByText('gateway.acme.example')).toBeTruthy()
    expect(screen.getByText(/Only continue if you trust/)).toBeTruthy()
    expect(screen.getByText(/includes an invitation/)).toBeTruthy()
    expect(desktop.probeConnectionConfig).not.toHaveBeenCalled()
    expect(desktop.applyConnectionConfig).not.toHaveBeenCalled()
  })

  it('does not mention an invitation when the link has none', async () => {
    installDesktop()
    render(<ConnectLinkDialog />)
    receive({ url: GATEWAY })

    await screen.findByText('gateway.acme.example')
    expect(screen.queryByText(/includes an invitation/)).toBeNull()
  })

  it('cancelling drops the link and its invitation without any request', async () => {
    const desktop = installDesktop()

    render(<ConnectLinkDialog />)
    receive({ url: GATEWAY, invite: TOKEN })
    fireEvent.click(await screen.findByText('Cancel'))

    await waitFor(() => expect($connectLinkRequest.get()).toBeNull())
    expect(desktop.probeConnectionConfig).not.toHaveBeenCalled()
    expect(desktop.api).not.toHaveBeenCalled()
  })

  it('continues into the remote form already pointed at the server', async () => {
    const desktop = installDesktop()

    render(<ConnectLinkDialog />)
    receive({ url: GATEWAY, invite: TOKEN })
    fireEvent.click(await screen.findByText('Continue'))

    const field = (await screen.findByPlaceholderText('https://gateway.example.com/hermes')) as HTMLInputElement
    expect(field.value).toBe(GATEWAY)
    await waitFor(() => expect(desktop.probeConnectionConfig).toHaveBeenCalledWith(GATEWAY))
  })

  it('redeems the invitation only after the connection is applied, then reports the profile', async () => {
    const desktop = installDesktop()
    const order: string[] = []

    desktop.applyConnectionConfig.mockImplementation(async () => {
      order.push('apply')

      return { mode: 'remote' }
    })
    desktop.api.mockImplementation(async () => {
      order.push('redeem')

      return { profile: 'alpha' }
    })

    render(<ConnectLinkDialog />)
    receive({ url: GATEWAY, invite: TOKEN })
    await continueAndApply(desktop)

    await waitFor(() => expect(desktop.api).toHaveBeenCalledTimes(1))
    expect(order).toEqual(['apply', 'redeem'])
    expect(desktop.api).toHaveBeenCalledWith({
      method: 'POST',
      path: '/api/access/invites/redeem',
      body: { token: TOKEN }
    })
    await waitFor(() => expect($notifications.get().some(n => /alpha/.test(String(n.message)))).toBe(true))
    expect($connectLinkRequest.get()).toBeNull()
  })

  it('never redeems when the link carried no invitation', async () => {
    const desktop = installDesktop()

    render(<ConnectLinkDialog />)
    receive({ url: GATEWAY })
    await continueAndApply(desktop)

    await waitFor(() => expect(desktop.applyConnectionConfig).toHaveBeenCalled())
    await waitFor(() => expect($connectLinkRequest.get()).toBeNull())
    expect(desktop.api).not.toHaveBeenCalled()
  })

  it('explains a refused invitation without leaking the token, and still drops it', async () => {
    const desktop = installDesktop()

    desktop.api.mockRejectedValue(new Error('400: {"detail":"invalid_invite"}'))
    render(<ConnectLinkDialog />)
    receive({ url: GATEWAY, invite: TOKEN })
    await continueAndApply(desktop)

    await waitFor(() => expect($notifications.get().some(n => /could not be used/i.test(String(n.title)))).toBe(true))
    expect(JSON.stringify($notifications.get())).not.toContain(TOKEN)
    expect($connectLinkRequest.get()).toBeNull()
  })

  it('rejects a malformed link with a notice instead of opening anything', () => {
    installDesktop()
    render(<ConnectLinkDialog />)

    let accepted = true

    act(() => {
      accepted = requestConnectFromDeepLink({ url: 'http://evil.example', invite: TOKEN })
    })

    expect(accepted).toBe(false)
    expect($connectLinkRequest.get()).toBeNull()
    expect($notifications.get().some(n => /not valid/i.test(String(n.message)))).toBe(true)
  })
})
