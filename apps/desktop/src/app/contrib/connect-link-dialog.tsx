import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { BrandMark } from '@/components/brand-mark'
import { FirstRunRemoteSetup } from '@/components/remote-setup/first-run'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { enConnectLink } from '@/i18n/en_first_run'
import { redeemInvite } from '@/lib/connect-link'
import { $connectLinkInvalid, $connectLinkRequest, clearConnectLink } from '@/store/connect-link'
import { notify } from '@/store/notifications'

/**
 * Confirmation for a `hermes://connect` link. The link is attacker-controllable, so nothing is contacted until the
 * user has seen the server's host and pressed Continue. Continue opens the ordinary remote-connection form already
 * pointed at that server; once the connection is applied, an invitation in the link (if any) is redeemed on it.
 * Sits above the first-run overlay, because a remote-only build shows that overlay while it waits for a server.
 */
export function ConnectLinkDialog() {
  const { t } = useI18n()
  const copy = t.firstRun.connectLink ?? enConnectLink
  const link = useStore($connectLinkRequest)
  const invalid = useStore($connectLinkInvalid)
  const [connecting, setConnecting] = useState(false)

  useEffect(() => {
    setConnecting(false)
  }, [link])

  if (invalid) {
    return (
      <div className="fixed inset-0 z-[1500] flex items-center justify-center bg-background/90 p-4 backdrop-blur-md">
        <div className="w-full max-w-md rounded-xl border border-(--stroke-nous) bg-card p-8 shadow-nous" role="alertdialog">
          <h2 className="text-xl font-semibold tracking-tight">{copy.invalidTitle}</h2>
          <p className="mt-3 text-sm text-muted-foreground">{copy.invalidBody}</p>
          <div className="mt-6 flex justify-end">
            <Button onClick={clearConnectLink} size="sm">
              {copy.ok}
            </Button>
          </div>
        </div>
      </div>
    )
  }

  if (!link) {
    return null
  }

  if (connecting) {
    // The token is captured here, before the form closes and the link is cleared: redeeming outlives the dialog.
    const invite = link.invite

    const redeem = () => {
      if (!invite) {
        return
      }

      void redeemInvite(request => window.hermesDesktop.api(request), invite).then(outcome =>
        outcome.ok
          ? notify({ kind: 'success', title: copy.redeemedTitle, message: copy.redeemedBody(outcome.profile) })
          : notify({ kind: 'error', title: copy.failedTitle, message: copy.failed[outcome.reason] })
      )
    }

    return (
      <div className="fixed inset-0 z-[1500]">
        <FirstRunRemoteSetup initialUrl={link.url} onApplied={redeem} onBack={clearConnectLink} />
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-[1500] flex items-center justify-center bg-background/90 p-4 backdrop-blur-md">
      <div className="w-full max-w-lg rounded-xl border border-(--stroke-nous) bg-card p-8 shadow-nous" role="dialog">
        <div className="flex items-start gap-4">
          <BrandMark className="size-11 shrink-0" />
          <div className="min-w-0">
            <h2 className="text-xl font-semibold tracking-tight">{copy.title}</h2>
            <div className="mt-4 text-xs font-medium text-muted-foreground">{copy.serverLabel}</div>
            <div className="mt-1 break-all font-mono text-lg">{link.host}</div>
          </div>
        </div>
        <p className="mt-5 text-sm text-muted-foreground">{copy.trustWarning}</p>
        <p className="mt-2 text-sm text-muted-foreground">{copy.switchNote}</p>
        {link.invite ? <p className="mt-2 text-sm text-muted-foreground">{copy.inviteNote}</p> : null}
        <div className="mt-7 flex items-center justify-end gap-2">
          <Button onClick={clearConnectLink} size="sm" variant="ghost">
            {copy.cancel}
          </Button>
          <Button onClick={() => setConnecting(true)} size="sm">
            {copy.continue}
          </Button>
        </div>
      </div>
    </div>
  )
}
