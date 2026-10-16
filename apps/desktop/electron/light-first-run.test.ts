import assert from 'node:assert/strict'

import { test } from 'vitest'

import { firstRunPolicy, mayStartLocalBootstrap } from './light-first-run'

const stamp = (payload: string) => ({ payload }) as never

test('the Light artifact is remote-only and never offers a local install', () => {
  assert.deepEqual(firstRunPolicy(stamp('light')), { remoteOnly: true })
  assert.equal(mayStartLocalBootstrap(stamp('light')), false)
})

test('every other artifact keeps the local-or-remote choice', () => {
  for (const payload of ['bundled', 'bootstrap']) {
    assert.deepEqual(firstRunPolicy(stamp(payload)), { remoteOnly: false })
    assert.equal(mayStartLocalBootstrap(stamp(payload)), true)
  }

  assert.equal(firstRunPolicy(null).remoteOnly, false)
  assert.equal(mayStartLocalBootstrap(null), true)
})

test('the policy carries no gateway URL: nothing company-specific is part of the artifact', () => {
  assert.deepEqual(Object.keys(firstRunPolicy(stamp('light'))), ['remoteOnly'])
})
