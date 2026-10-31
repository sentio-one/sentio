export function deepLinkSchemes(development: boolean): string[] {
  return development ? ['sentio', 'hermes', 'sentio-dev', 'hermes-dev'] : ['sentio', 'hermes']
}

export function registeredDeepLinkSchemes(development: boolean): string[] {
  return development ? ['sentio-dev', 'hermes-dev'] : ['sentio', 'hermes']
}

export function isDeepLinkScheme(scheme: string, development: boolean): boolean {
  return deepLinkSchemes(development).includes(scheme)
}
