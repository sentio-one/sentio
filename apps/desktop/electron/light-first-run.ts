/**
 * First-run policy of the remote-only ("Light") artifact.
 *
 * Light carries no agent runtime, so its first launch must never offer the local install: it goes
 * straight to the remote-connection form. No gateway URL is baked in (one artifact serves every
 * company); the user, or whoever provisions them, enters it. Every other artifact keeps the
 * local-or-remote choice.
 */
import type { InstallStamp } from './install-stamp'

type StampLike = Pick<InstallStamp, 'payload'> | null | undefined

export function isLightArtifact(stamp: StampLike): boolean {
  return stamp?.payload === 'light'
}

export function firstRunPolicy(stamp: StampLike): { remoteOnly: boolean } {
  return { remoteOnly: isLightArtifact(stamp) }
}

/** False for Light: there is no runtime to install, whatever the renderer asks for. */
export function mayStartLocalBootstrap(stamp: StampLike): boolean {
  return !isLightArtifact(stamp)
}
