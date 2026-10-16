import assert from 'node:assert/strict'

import { test } from 'vitest'

import { firstRunPolicy, mayStartLocalBootstrap, normalizeGatewayUrl } from './light-first-run'

const stamp = (payload: string) => ({ payload }) as never

test('the Light artifact is remote-only and never offers a local install', () => {
  assert.deepEqual(firstRunPolicy(stamp('light'), 'https://gateway.corp.example'), {
    remoteOnly: true,
    defaultGatewayUrl: 'https://gateway.corp.example'
  })
  assert.equal(mayStartLocalBootstrap(stamp('light')), false)
})

test('every other artifact keeps the local-or-remote choice and ignores the preset URL', () => {
  for (const payload of ['bundled', 'bootstrap']) {
    assert.deepEqual(firstRunPolicy(stamp(payload), 'https://gateway.corp.example'), {
      remoteOnly: false,
      defaultGatewayUrl: ''
    })
    assert.equal(mayStartLocalBootstrap(stamp(payload)), true)
  }

  assert.equal(firstRunPolicy(null, 'https://gateway.corp.example').remoteOnly, false)
  assert.equal(mayStartLocalBootstrap(null), true)
})

test('an unset or unusable preset leaves the remote form empty rather than guessing', () => {
  for (const bad of ['', '   ', 'not a url', 'ftp://gateway.corp.example', 'https://user:pw@gateway.corp.example']) {
    assert.equal(firstRunPolicy(stamp('light'), bad).defaultGatewayUrl, '', bad)
  }
})

test('gateway URLs are normalised to an https origin plus path, without a trailing slash', () => {
  assert.equal(normalizeGatewayUrl(' https://gateway.corp.example/ '), 'https://gateway.corp.example')
  assert.equal(normalizeGatewayUrl('https://corp.example/hermes/'), 'https://corp.example/hermes')
  assert.equal(normalizeGatewayUrl('https://corp.example:9119'), 'https://corp.example:9119')
})

test('plain http is accepted only for a loopback gateway (local development)', () => {
  assert.equal(normalizeGatewayUrl('http://127.0.0.1:9119'), 'http://127.0.0.1:9119')
  assert.equal(normalizeGatewayUrl('http://localhost:9119/'), 'http://localhost:9119')
  assert.equal(normalizeGatewayUrl('http://gateway.corp.example'), '')
})

test('credentials, query strings and fragments are never baked into the preset', () => {
  for (const bad of [
    'https://u:p@gateway.corp.example',
    'https://gateway.corp.example?token=1',
    'https://gateway.corp.example#x'
  ]) {
    assert.equal(normalizeGatewayUrl(bad), '', bad)
  }
})
