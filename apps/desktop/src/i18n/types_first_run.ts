/** `hermes://connect` links: the confirmation screen and the outcome of an invitation. */
export interface ConnectLinkCopy {
  title: string
  serverLabel: string
  trustWarning: string
  inviteNote: string
  switchNote: string
  cancel: string
  continue: string
  invalidTitle: string
  invalidBody: string
  redeemedTitle: string
  redeemedBody: (profile: string) => string
  failedTitle: string
  failed: { invalid: string; rate_limited: string; not_supported: string; unreachable: string }
}

/** First-run onboarding: the setup_beat cards and their receipts, Skip setup, the local-model offer
 *  at the end, and the Settings row that runs it again. */
export interface FirstRunCopy {
  /** Optional: the other locales' whole-block files predate it, and overlays fall back to English. */
  connectLink?: ConnectLinkCopy
  /** Receipt labels, one per beat. */
  beats: {
    name: string
    theme: string
    layout: string
    connectors: string
    plugins: string
    tour: string
    build: string
  }
  namePlaceholder: string
  /** The OS account's full name, offered as a row on the name card. */
  useName: (name: string) => string
  install: string
  installed: string
  loading: string
  noPlugins: string
  /** A receipt for a card that settled with nothing chosen (no apps connected, no plugin installed). */
  noneChosen: string
  noAnswer: string
  /** A card answer the backend refused; the model brings the card back. */
  answerRefused: string
  /** The tour card's options by id; the backend's labels are English. */
  tours: {
    quick: string
    full: string
    skip: string
  }
  skippedAll: string
  skip: string
  skipTip: string
  skipFailed: string
  localModel: {
    title: string
    text: (model: string, size: string) => string
    setUp: string
    notNow: string
  }
  /** The first-run intro typed into the empty setup chat, two lines. The backend's scripted
   *  greeting (_GREETINGS in agent/onboarding_text.py) is the two joined: keep them equal. */
  intro: {
    greeting: string
    plan: string
  }
  rerun: {
    title: string
    description: string
    action: string
    failed: string
  }
}
