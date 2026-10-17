import { ConnectLinkDialog } from './connect-link-dialog'
import { McpInstallDeepLinkDialog } from './mcp-install-deeplink-dialog'

/** The confirmation dialogs a `hermes://` deep link can open; each stays closed until its own request arrives. */
export function DeepLinkDialogs() {
  return (
    <>
      <McpInstallDeepLinkDialog />
      <ConnectLinkDialog />
    </>
  )
}
