import { useState } from 'react'

import { BrandMark } from '@/components/brand-mark'
import type { DesktopBootstrapSetupChoice } from '@/global'
import { useI18n } from '@/i18n'
import { AlertCircle, Globe, Loader2, Monitor } from '@/lib/icons'

import { localCardPresentation } from './desktop-install-local-card'

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err || 'Unknown error')
}

interface SetupChoiceProps {
  choice: DesktopBootstrapSetupChoice
  /** Open the remote-connection form instead of installing locally. */
  onRemote: () => void
}

/** First-run choice: connect to an existing Hermes, or install/use the local runtime. */
export function SetupChoice({ choice, onRemote }: SetupChoiceProps) {
  const { t } = useI18n()
  const copy = t.install
  const localState = choice.local
  const localPres = localCardPresentation(localState)

  // The choice remains mounted while main hands off to local bootstrap. Once
  // a manifest/failure takes ownership (or a later repair presents a fresh
  // choice), this transient button state must not leak across phases — so it
  // records the root it was produced under and is read back only under that
  // same root. Deriving it beats clearing it in an effect: the choice paints
  // as soon as the first snapshot commits, and a click landing before such an
  // effect flushed would have its error wiped before it ever rendered.
  const [localStart, setLocalStart] = useState<{
    root: string | null
    starting: boolean
    error: string | null
  }>({ root: null, starting: false, error: null })

  const activeRoot = choice.activeRoot ?? null
  const forActiveRoot = localStart.root === activeRoot
  const localStarting = forActiveRoot && localStart.starting
  const localStartError = forActiveRoot ? localStart.error : null

  return (
    <div className="fixed inset-0 z-(--z-setup) flex items-center justify-center bg-background/90 p-4 backdrop-blur-md">
      <div className="w-full max-w-2xl rounded-xl border border-(--stroke-nous) bg-card p-8 shadow-nous">
        <div className="flex items-start gap-4">
          <BrandMark className="size-11 shrink-0" />
          <div className="min-w-0">
            <h2 className="text-xl font-semibold tracking-tight">{copy.setupChoiceTitle}</h2>
            <p className="mt-1.5 text-sm text-muted-foreground">
              {localState === 'none' ? copy.setupChoiceDesc : copy.setupChoiceDescLocal}
            </p>
          </div>
        </div>

        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          <button
            className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) p-4 text-left transition hover:bg-(--chrome-action-hover)"
            onClick={onRemote}
            type="button"
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              <Globe className="size-4 text-muted-foreground" />
              <span>{copy.connectExistingTitle}</span>
            </div>
            <p className="mt-2 text-sm leading-5 text-muted-foreground">{copy.connectExistingDesc}</p>
          </button>

          <button
            className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) p-4 text-left transition hover:bg-(--chrome-action-hover) disabled:cursor-not-allowed disabled:opacity-60"
            disabled={localStarting}
            onClick={async () => {
              setLocalStart({ root: activeRoot, starting: true, error: null })

              try {
                const desktop = window.hermesDesktop

                if (!desktop || typeof desktop.continueBootstrapLocal !== 'function') {
                  throw new Error(copy.localStartUnavailable)
                }

                await desktop.continueBootstrapLocal()
              } catch (err) {
                setLocalStart({ root: activeRoot, starting: false, error: errorMessage(err) })
              }
            }}
            type="button"
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              {localStarting ? (
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
              ) : (
                <Monitor className="size-4 text-muted-foreground" />
              )}
              <span>{copy[localPres.title]}</span>
            </div>
            <p className="mt-2 text-sm leading-5 text-muted-foreground">{copy[localPres.desc]}</p>
          </button>
        </div>

        {localStartError ? (
          <div className="mt-4 flex items-start gap-2 text-sm text-destructive">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <span>{localStartError}</span>
          </div>
        ) : null}

        {localPres.showInstallTo ? (
          <div className="mt-6 text-xs text-muted-foreground">
            {copy.installTo}{' '}
            <code className="font-mono text-(--ui-text-secondary)">{choice.activeRoot}</code>
          </div>
        ) : null}
      </div>
    </div>
  )
}
