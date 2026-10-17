import { describe, expect, it, vi } from 'vitest'

import { normalizeGatewayUrl, parseConnectLink, redeemInvite } from './connect-link'

const TOKEN = 'A'.repeat(43)

describe('parseConnectLink', () => {
  it('accepts a gateway URL with an invitation', () => {
    expect(parseConnectLink({ url: 'https://gateway.acme.example', invite: TOKEN })).toEqual({
      url: 'https://gateway.acme.example',
      host: 'gateway.acme.example',
      invite: TOKEN
    })
  })

  it('accepts a link with no invitation', () => {
    expect(parseConnectLink({ url: 'https://gateway.acme.example' })?.invite).toBeNull()
  })

  it('normalises the URL and shows the port in the host', () => {
    expect(parseConnectLink({ url: ' https://gw.acme.example:9119/hermes/ ' })).toMatchObject({
      url: 'https://gw.acme.example:9119/hermes',
      host: 'gw.acme.example:9119'
    })
  })

  it('shows a lookalike (non-ASCII) host in its safe punycode form, never as it would render', () => {
    expect(parseConnectLink({ url: 'https://\u0430pple.example' })?.host).toBe('xn--pple-43d.example')
  })

  it.each([
    [{}],
    [{ invite: TOKEN }],
    [{ url: '' }],
    [{ url: 'not a url' }],
    [{ url: 'http://gateway.acme.example' }],
    [{ url: 'ftp://gateway.acme.example' }],
    [{ url: 'javascript:alert(1)' }],
    [{ url: 'file:///C:/Windows' }],
    [{ url: 'https://user:pw@gateway.acme.example' }],
    [{ url: 'https://gateway.acme.example?token=1' }],
    [{ url: 'https://gateway.acme.example#x' }],
    [{ url: `https://gateway.acme.example/${'a'.repeat(3000)}` }]
  ])('rejects an unsafe or malformed link %j', params => {
    expect(parseConnectLink(params as Record<string, string>)).toBeNull()
  })

  it.each(['short', 'has space in it xxxxxxxxxxxxxxxxxxxxxxxx', 'x'.repeat(201), 'bad/chars+++++++++++++++++++++'])(
    'rejects the whole link when the invitation is malformed (%s)',
    invite => {
      expect(parseConnectLink({ url: 'https://gateway.acme.example', invite })).toBeNull()
    }
  )

  it('allows plain http only for a loopback gateway', () => {
    expect(parseConnectLink({ url: 'http://127.0.0.1:9119' })?.host).toBe('127.0.0.1:9119')
    expect(parseConnectLink({ url: 'http://localhost:9119/' })?.url).toBe('http://localhost:9119')
    expect(parseConnectLink({ url: 'http://192.168.1.5:9119' })).toBeNull()
  })

  it('ignores parameters it does not know, so a link cannot smuggle options in', () => {
    expect(parseConnectLink({ url: 'https://gw.acme.example', profile: 'admin', token: 'x' })).toEqual({
      url: 'https://gw.acme.example',
      host: 'gw.acme.example',
      invite: null
    })
  })
})

describe('normalizeGatewayUrl', () => {
  it('returns an empty string for anything that is not a safe gateway origin', () => {
    expect(normalizeGatewayUrl('https://gw.acme.example/')).toBe('https://gw.acme.example')
    expect(normalizeGatewayUrl('')).toBe('')
    expect(normalizeGatewayUrl('https://gw.acme.example?x=1')).toBe('')
  })
})

describe('redeemInvite', () => {
  const noWait = { attempts: 3, sleep: () => Promise.resolve() }

  it('posts the token to the redeem route and returns the granted profile', async () => {
    const api = vi.fn().mockResolvedValue({ profile: 'alpha' })

    expect(await redeemInvite(api, TOKEN, noWait)).toEqual({ ok: true, profile: 'alpha' })
    expect(api).toHaveBeenCalledWith({ method: 'POST', path: '/api/access/invites/redeem', body: { token: TOKEN } })
  })

  it.each([
    ['400: {"detail":"invalid_invite"}', 'invalid'],
    ['Error: 429: {"detail":"too_many_attempts"}', 'rate_limited'],
    ['409: {"detail":"access_policy_not_configured"}', 'not_supported']
  ])('stops at once on a final answer (%s)', async (message, reason) => {
    const api = vi.fn().mockRejectedValue(new Error(message))

    expect(await redeemInvite(api, TOKEN, noWait)).toEqual({ ok: false, reason })
    expect(api).toHaveBeenCalledTimes(1)
  })

  it('retries while the new connection settles, then succeeds', async () => {
    const api = vi
      .fn()
      .mockRejectedValueOnce(new Error('fetch failed'))
      .mockRejectedValueOnce(new Error('401: {"error":"unauthenticated"}'))
      .mockResolvedValue({ profile: 'alpha' })

    expect(await redeemInvite(api, TOKEN, noWait)).toEqual({ ok: true, profile: 'alpha' })
    expect(api).toHaveBeenCalledTimes(3)
  })

  it('gives up as unreachable when the server never answers', async () => {
    const api = vi.fn().mockRejectedValue(new Error('ECONNREFUSED'))

    expect(await redeemInvite(api, TOKEN, noWait)).toEqual({ ok: false, reason: 'unreachable' })
    expect(api).toHaveBeenCalledTimes(3)
  })

  it('never puts the token in an outcome it reports', async () => {
    const api = vi.fn().mockRejectedValue(new Error(`boom ${TOKEN}`))
    const outcome = await redeemInvite(api, TOKEN, noWait)

    expect(JSON.stringify(outcome)).not.toContain(TOKEN)
  })

  it('treats a response without a profile as a failure rather than success', async () => {
    const api = vi.fn().mockResolvedValue({})

    expect(await redeemInvite(api, TOKEN, noWait)).toEqual({ ok: false, reason: 'invalid' })
  })
})
