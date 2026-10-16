import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { test } from 'vitest'

import { lightGatewayUrl } from './bundle-electron-main.mjs'

function source(defaults) {
  const root = mkdtempSync(join(tmpdir(), 'light-src-'))
  mkdirSync(join(root, 'apps/desktop'), { recursive: true })
  if (defaults !== undefined) writeFileSync(join(root, 'apps/desktop/light-defaults.json'), defaults)
  return root
}

test('the build variable wins over the committed default', () => {
  const root = source('{"gatewayUrl":"https://file.example"}')
  assert.equal(lightGatewayUrl(root, { HERMES_LIGHT_GATEWAY_URL: ' https://env.example ' }), 'https://env.example')
})

test('the committed default applies when the variable is unset or blank', () => {
  const root = source('{"gatewayUrl":"https://file.example"}')
  assert.equal(lightGatewayUrl(root, {}), 'https://file.example')
  assert.equal(lightGatewayUrl(root, { HERMES_LIGHT_GATEWAY_URL: '  ' }), 'https://file.example')
})

test('no preset anywhere is an empty string, not an error', () => {
  assert.equal(lightGatewayUrl(source(), {}), '')
  assert.equal(lightGatewayUrl(source('{"gatewayUrl":""}'), {}), '')
  assert.equal(lightGatewayUrl(source('{}'), {}), '')
})

test('a malformed defaults file fails the build instead of silently shipping no preset', () => {
  assert.throws(() => lightGatewayUrl(source('{ nope'), {}), /light-defaults\.json/)
  assert.throws(() => lightGatewayUrl(source('{"gatewayUrl": 7}'), {}), /gatewayUrl/)
})
