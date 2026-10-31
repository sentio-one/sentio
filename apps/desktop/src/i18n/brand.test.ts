import { describe, expect, it } from 'vitest'

import { brandMessage, brandTranslations } from './brand'
import { TRANSLATIONS } from './catalog'
import { resolveTranslations } from './registry'
import { translateFrom } from './runtime'

describe('Sentio branding', () => {
  it('rewrites product names and standalone Hermes in translated text', () => {
    expect(brandMessage('Hermes Desktop, Hermes Agent, Hermes Light, and Hermes')).toBe(
      'Sentio One, Sentio One, Sentio One, and Sentio One'
    )
  })

  it('keeps URLs, code, identifiers, and Nous model names unchanged', () => {
    const input = 'Hermes at https://example.com/Hermes, `Hermes`, HERMES_HOME, hermes-agent, hermes://open, Hermes 3, Hermes 4, Nous-Hermes-2, Hermes-3-Llama'

    expect(brandMessage(input)).toBe('Sentio One at https://example.com/Hermes, `Hermes`, HERMES_HOME, hermes-agent, hermes://open, Hermes 3, Hermes 4, Nous-Hermes-2, Hermes-3-Llama')
  })

  it('rewrites strings returned by function-valued messages', () => {
    const branded = brandTranslations({
      intro: {
        custom: (name: string) => `Hermes is using ${name}`
      }
    })

    expect((branded.intro as { custom: (name: string) => string }).custom('Hermes Agent')).toBe(
      'Sentio One is using Sentio One'
    )
  })

  it('rewrites plugin bundle results resolved through the runtime translator', () => {
    const pluginBundle = () => ({ title: 'Hermes Desktop', message: (name: string) => `Hello, ${name}` })

    expect(translateFrom(pluginBundle, 'en', 'title', [])).toBe('Sentio One')
    expect(translateFrom(pluginBundle, 'en', 'message', ['Hermes'])).toBe('Hello, Sentio One')
  })

  it('keeps resolved English and bundled locale catalogs branded', () => {
    const locales = Object.keys(TRANSLATIONS) as Array<keyof typeof TRANSLATIONS>

    for (const locale of ['en', ...locales]) {
      const visit = (value: unknown, path: string[] = []): void => {
        if (typeof value === 'string') {
          expect(brandMessage(value), `${locale}:${path.join('.')}`).toBe(value)
        } else if (Array.isArray(value)) {
          value.forEach((item, index) => visit(item, [...path, String(index)]))
        } else if (value && typeof value === 'object') {
          Object.entries(value).forEach(([key, child]) => visit(child, [...path, key]))
        }
      }

      visit(resolveTranslations(locale))
    }
  })
})
