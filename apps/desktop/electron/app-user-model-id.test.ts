import { describe, expect, it } from 'vitest'

import { appUserModelId } from './app-user-model-id'

const STABLE_ID = 'com.nousresearch.hermes'

describe('appUserModelId', () => {
  it('gives a Light build its own app id, so the running window matches the shortcut the installer created', () => {
    const light = { appId: 'com.nousresearch.hermes-light', light: true }

    expect(appUserModelId(light, null)).toBe('com.nousresearch.hermes-light')
    expect(appUserModelId(light, null)).not.toBe(STABLE_ID)
  })

  it('keeps a nonstable build on its own app id', () => {
    const commitBuild = { appId: 'com.nousresearch.hermes-light-b0ae35e', light: true }

    expect(appUserModelId(commitBuild, 'Sentio One b0ae35e')).toBe('com.nousresearch.hermes-light-b0ae35e')
  })

  it('leaves non-Light artifacts on the stable id the MSIX package identity depends on', () => {
    expect(appUserModelId({ appId: 'com.nousresearch.hermes', light: false }, null)).toBe(STABLE_ID)
    expect(appUserModelId({ appId: 'com.nousresearch.hermes-bundled', light: false }, null)).toBe(STABLE_ID)
  })

  it('lets a named nonstable non-Light build keep its own app id', () => {
    expect(appUserModelId({ appId: 'com.nousresearch.hermes-canary', light: false }, 'Hermes Canary')).toBe(
      'com.nousresearch.hermes-canary'
    )
  })
})
