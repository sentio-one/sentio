import type { ConnectLinkCopy, FirstRunCopy } from './types_first_run'

export const enConnectLink: ConnectLinkCopy = {
  title: 'Connect to this Hermes server?',
  serverLabel: 'Server',
  trustWarning:
    'Only continue if you trust whoever sent you this link. Hermes will open this server and ask you to sign in there.',
  inviteNote:
    'This link includes an invitation. It is redeemed after you sign in and gives you access to the profile it names.',
  switchNote: 'Hermes will use this server instead of the one it is connected to now.',
  cancel: 'Cancel',
  continue: 'Continue',
  invalidTitle: 'Link not recognised',
  invalidBody: 'That Hermes link is not valid or is not safe to open.',
  redeemedTitle: 'Invitation accepted',
  redeemedBody: profile => `You now have access to the \u201c${profile}\u201d profile.`,
  failedTitle: 'Invitation could not be used',
  failed: {
    invalid:
      'The invitation is not valid, has expired, was already used or was withdrawn. Ask your administrator for a new one.',
    rate_limited: 'Too many attempts. Wait a few minutes, then open the link again.',
    not_supported: 'This server does not use invitations.',
    unreachable: 'Could not reach the server to accept the invitation. Open the link again once you are connected.'
  }
}

export const enFirstRun: FirstRunCopy = {
  connectLink: enConnectLink,
  beats: {
    name: 'Name',
    theme: 'Accent color',
    layout: 'Layout',
    connectors: 'Apps',
    plugins: 'Plugins',
    tour: 'Tour',
    build: 'First task'
  },
  namePlaceholder: 'Your name',
  useName: name => `Use “${name}”`,
  install: 'Install',
  installed: 'Installed',
  loading: 'Loading…',
  noPlugins: 'No plugins to show right now.',
  noneChosen: 'None',
  noAnswer: 'No answer',
  answerRefused: 'Answer not accepted',
  tours: {
    quick: 'Quick tour',
    full: 'Show me everything',
    skip: "Skip, let's build something"
  },
  skippedAll: 'Setup skipped',
  skip: 'Skip setup',
  skipTip: 'End the guided setup and open a new chat.',
  skipFailed: 'Could not skip setup',
  localModel: {
    title: 'This computer can run a model locally',
    text: (model, size) => `${model} (${size}) fits your hardware. It runs free, and your chats stay on this computer.`,
    setUp: 'Set it up',
    notNow: 'Not now'
  },
  intro: {
    greeting: "Hi, I'm Hermes.",
    plan: "Let's set things up for you. Then we'll get something cool done."
  },
  rerun: {
    title: 'Run onboarding again',
    description: 'Open a new setup chat that walks through the first-run steps with your current model.',
    action: 'Run again',
    failed: 'Could not start onboarding'
  }
}
