// Spans left alone: code spans, URLs, Nous's model names, and identifier-shaped tokens (HERMES_HOME, hermes_cli,
// hermes-agent, X-Hermes-Session-Token). Identifiers are matched case-sensitively on purpose: a capitalised hyphen
// compound such as the German "Hermes-Gateway" is prose and must be branded.
const PROTECTED_SPANS = /(?<!`)`+[^`]*`+(?!`)|\b[A-Za-z][A-Za-z0-9+.-]*:\/\/[^\s<>"'`]+|\bNous-Hermes(?:-[A-Za-z0-9]+)*\b|\bHermes(?:\s+[34]|-[0-9]+-[A-Za-z0-9-]+)\b|\b[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_-]*\b|\b[a-z][a-z0-9]*-[A-Za-z0-9_-]*\b|\bX-[A-Za-z0-9-]+\b/g
const PRODUCT_NAME = /\b[Hh]ermes\s+[Dd]esktop\b|\bHermes(?:\s+(?:Agent|Light))?\b/g

/** Replace user-facing Hermes product references while preserving code and identifiers. */
export function brandMessage(value: string): string {
  let result = ''
  let cursor = 0

  for (const match of value.matchAll(PROTECTED_SPANS)) {
    const start = match.index ?? cursor
    result += value.slice(cursor, start).replace(PRODUCT_NAME, 'Sentio One') + match[0]
    cursor = start + match[0].length
  }

  return result + value.slice(cursor).replace(PRODUCT_NAME, 'Sentio One')
}

/** Brand a merged message tree without changing the upstream or plugin catalogs. */
export function brandTranslations<T>(value: T): T {
  if (typeof value === 'string') {
    return brandMessage(value) as T
  }

  if (typeof value === 'function') {
    const message = value as (...args: unknown[]) => unknown

    return ((...args: unknown[]) => {
      const result = message(...args)

      return typeof result === 'string' ? brandMessage(result) : result
    }) as T
  }

  if (Array.isArray(value)) {
    return value.map(item => brandTranslations(item)) as T
  }

  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, child]) => [key, brandTranslations(child)])
    ) as T
  }

  return value
}
