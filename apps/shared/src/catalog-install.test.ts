import { describe, expect, it } from 'vitest'

import { pluginCatalogInstallUrl, skillCatalogInstallUrl } from './catalog-install'

describe('catalog install deep links', () => {
  it('generates Sentio links for desktop installs', () => {
    expect(skillCatalogInstallUrl({ name: 'example', source: 'official', identifier: 'official/example' })).toBe(
      'sentio://skill/install?identifier=official%2Fexample'
    )
    expect(pluginCatalogInstallUrl({ name: 'example-plugin' })).toBe(
      'sentio://plugin/install?catalog=example-plugin'
    )
  })
})
