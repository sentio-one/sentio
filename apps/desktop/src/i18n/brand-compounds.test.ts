import { describe, expect, it } from 'vitest'

import { brandMessage } from './brand'
import { TRANSLATIONS } from './catalog'
import { resolveTranslations } from './registry'

describe('Sentio branding of compounds and lowercase names', () => {
  it('rewrites Hermes inside hyphenated compounds, as languages like German write them', () => {
    expect(brandMessage('Das Hermes-Gateway ist nicht erreichbar')).toBe('Das Sentio One-Gateway ist nicht erreichbar')
    expect(brandMessage('mit dem Remote-Hermes-Backend')).toBe('mit dem Remote-Sentio One-Backend')
  })

  it.each([
    'HERMES_HOME',
    'hermes_cli',
    'hermes-agent',
    'hermes-dev',
    'X-Hermes-Session-Token',
    'Nous-Hermes-2',
    'Hermes-3-Llama-3.1'
  ])('still protects the identifier-shaped token %s', token => {
    expect(brandMessage(`see ${token} here`)).toBe(`see ${token} here`)
  })

  it('rewrites a lowercase "hermes desktop" product name but leaves commands and binaries alone', () => {
    expect(brandMessage('Source + hermes desktop')).toBe('Source + Sentio One')
    expect(brandMessage('Run hermes update, then check the remote hermes binary')).toBe(
      'Run hermes update, then check the remote hermes binary'
    )
  })

  it('names the product, not the command, in the /quit description of every locale', () => {
    for (const locale of Object.keys(TRANSLATIONS) as Array<keyof typeof TRANSLATIONS>) {
      const descs = (resolveTranslations(locale) as unknown as { composer: { commandDescs: Record<string, string> } })
        .composer.commandDescs

      expect(descs['/quit'], locale).not.toMatch(/hermes/i)
      expect(descs['/quit'], locale).toContain('Sentio One')
    }
  })
})
