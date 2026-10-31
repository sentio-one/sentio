import assert from 'node:assert/strict'

import { test } from 'vitest'

import { deepLinkSchemes, isDeepLinkScheme, registeredDeepLinkSchemes } from './deep-link-schemes'

test('packaged links accept Sentio and legacy Hermes schemes only', () => {
  assert.deepEqual(deepLinkSchemes(false), ['sentio', 'hermes'])
  assert.equal(isDeepLinkScheme('sentio', false), true)
  assert.equal(isDeepLinkScheme('hermes', false), true)
  assert.equal(isDeepLinkScheme('sentio-dev', false), false)
  assert.equal(isDeepLinkScheme('other', false), false)
})

test('development links accept both dev schemes and their packaged counterparts', () => {
  assert.deepEqual(deepLinkSchemes(true), ['sentio', 'hermes', 'sentio-dev', 'hermes-dev'])
  assert.deepEqual(registeredDeepLinkSchemes(true), ['sentio-dev', 'hermes-dev'])
  assert.equal(isDeepLinkScheme('sentio-dev', true), true)
  assert.equal(isDeepLinkScheme('hermes-dev', true), true)
  assert.equal(isDeepLinkScheme('sentio', true), true)
  assert.equal(isDeepLinkScheme('hermes', true), true)
})
