# A `stable` branch for tester and client deployments

Status: design, approved in chat 2026-10-30. Implementation not started.

## Goal

`main` moves on every merge. Testers and client companies need a line that only moves when a build has been tested. The intended
workflow is: **merge to `stable` and every client deployment updates.**

## Decisions

1. **`stable` is a long-lived branch promoted from `main` by pull request.** `main` stays the hot line.
2. **Every client Dokploy compose project deploys from `stable`** with auto-deploy on (`deploy/dokploy/README.md` already deploys
   from "the branch you deploy from"). Merging to `stable` rebuilds and redeploys all of them. The maintainer's own development
   company may stay on `main`.
3. **Release tags are cut from `stable`.** The release marked *Latest* is always a promoted build. `main-latest` stays the hot
   pre-release. This amends L5 in `FORK_DECISIONS.md`: a release is a version bump that has been promoted to `stable`.
4. **Hotfixes land on `main` first and are cherry-picked into `stable`**, so the lines do not diverge.
5. **Versioning (decided 2026-10-30).** The first stable release is `0.3.0`. Each later release bumps the patch (`0.3.1` ... `0.3.9`);
   after `X.Y.9` the next release is `X.(Y+1).0` (so `0.3.9` is followed by `0.4.0`). The patch never exceeds 9. The version bump rides
   in the PR that is promoted to `stable`. Builds from `main` keep the last released version and are told apart by commit.
6. **`stable` starts at the then-current `main`**, after the pending preview-bridge PR (#28) merges, because that is what has
   been tested.

## Changes

| Area | Change |
|---|---|
| `.github/rulesets/stable.json` | New. Modeled on `main.json` but targeting `refs/heads/stable`: pull request, 1 approval, merge commits only, the same four required status checks, no deletion, no non-fast-forward. |
| `scripts/fork/check-github-protection.sh`, `apply-github-protection.sh` | Cover `stable` as well as the default branch. `protection-drift.yml` then guards it weekly. |
| `scripts/fork/promote-stable.sh` | New. Opens a PR from `main` into `stable` listing the commits being promoted. Never pushes to `stable` directly. |
| `.github/workflows/tag-release.yml` | Trigger on pushes to `stable`, not `main`; guard `github.ref == 'refs/heads/stable'`. A version bump merged to `main` does not tag until promoted. |
| `.github/workflows/release-light.yml` | Unchanged for `main` (still refreshes `main-latest`). A `v*` tag already builds and publishes regardless of branch. |
| `scripts/fork/tag-release.sh` | Refuse to tag a commit that is not on `origin/stable`, and a version whose patch is above 9. |
| `scripts/fork/bump-version.sh` | With no argument (or `next`), compute the next version by the rule above; an explicit version is still accepted but must follow the rule (patch 0-9) and be newer. |
| Dokploy (maintainer, manual) | Point each client project at branch `stable`. Not expressible in this repository. |
| Docs | `RELEASING.md`, `deploy/dokploy/README.md`, `REPO_PROTECTION.md`, `FORK_DECISIONS.md` (amend L5, add a decision). |

## Maintainer-only steps (repository admin)

Create the `stable` branch, apply the ruleset, move the `RELEASE_TOKEN` workflow trigger, repoint Dokploy. The implementation
documents the exact clicks.

## Out of scope / open

- **Client updates are manual for now (decided 2026-10-30).** Backends redeploy on merge to `stable`; testers reinstall the Light
  MSIX from the new release. Verified in code: Windows Light declares update mechanism `external`
  (`apps/desktop/electron/updater/updater.test.ts`), so it has no in-app updater. The bundled Windows build gets one-click updates
  from an App Installer feed (`.appinstaller`), which needs a **signed** MSIX and a **hosted feed**; Light is unsigned and has
  neither. Follow-up options, not part of this work: (a) sign Light and publish an App Installer feed for a real update button;
  (b) a small in-app "new version available" notice that links to the latest release.
- **Client and server skew.** Both come from `stable`, so skew is limited to the window between a backend redeploy and a client
  reinstall.
- Google OIDC verification, desktop and sign-up UI, and the website are separate sub-projects.
