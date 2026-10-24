# Repository protection

How `sentio-one/sentio` is protected, what was found wrong on 2026-10-23, and how to apply and verify the fix. The rules live as
code in `.github/rulesets/*.json`; `scripts/fork/apply-github-protection.sh` applies them and
`scripts/fork/check-github-protection.sh` verifies what GitHub actually enforces.

## Why this matters more than usual

- **A push to `main` is a production deploy.** Dokploy auto-deploys the branch it tracks (`deploy/dokploy/README.md`), so whoever can
  push to `main` can change what runs for every company.
- **A pushed `v*` tag publishes the desktop installer** (`release-light.yml` has `contents: write`).
- **A squash merge of an upstream merge** drops upstream's commits from our ancestry and makes the next merge conflict on everything
  the fork touched (`FORK_DECISIONS.md` section 3).

## Status: applied (2026-10-24)

The owner applied it with `scripts/fork/apply-github-protection.sh --apply --replace-legacy`. Verified afterwards: the checker ends
`github protection: ok`; `GET /repos/sentio-one/sentio/rules/branches/main` lists deletion, non-fast-forward, pull request and
required status checks (and `upstream-merge` lists deletion and non-fast-forward); merge commits are the only allowed method; secret
scanning, push protection and private vulnerability reporting are on; workflow tokens default to read-only. The two original rulesets
were removed. `Protection drift` runs weekly and on demand.

**Day to day:** every change to `main` is a pull request that needs one approval and the four `Fork CI` jobs. The pull requests are
opened under `TheophilusChinomona`, so `sentio-one` approves them (an author cannot approve their own). As owner, `sentio-one` may
also merge through the pull request without the approval (the ruleset's bypass is "pull request only", never a direct push).

## What was found (2026-10-23, read from the API)

| Finding | Effect |
|---|---|
| Both rulesets (`protect-main-1`, `protect-main-2`) include `refs/heads/protect-main`, a branch that does not exist | **`main` had no protection at all.** The rulesets were "active", which looks reassuring and protects nothing. Direct pushes to `main` worked. |
| `protect-main-2` requires a pull request but allows merge, squash and rebase | Even if it applied to `main`, a squash of an upstream merge would be allowed. |
| No required status checks | A red `Fork CI` would not block a merge. |
| No rule on `v*` tags | Any collaborator with write access can publish a release by pushing a tag. |
| `upstream-merge` (a long-lived branch) can be deleted or force-pushed | One mistaken click or push loses the upstream history we merge from. |
| Squash and rebase merging are enabled in the repository settings | Same risk as above, through the UI. |
| The `TheophilusChinomona` account has **write** access, not admin; the repo belongs to the personal account `sentio-one` | Protection can only be changed by the owner account. |

The checker catches this class of mistake: it fails on any ruleset that names an exact branch that does not exist.

## Target configuration

| Where | Rule | Why |
|---|---|---|
| Ruleset `main` (`~DEFAULT_BRANCH`) | Pull request required, **1 approval**, stale approvals dismissed on push | Nothing reaches production unreviewed. 1 approval keeps the earlier choice of the owner. |
| | **Merge commits only** (`allowed_merge_methods: ["merge"]`) | Keeps upstream's ancestry. Applies to every PR on `main`; feature PRs also use a merge commit. |
| | No deletion, no force push | |
| | Required checks: the four `Fork CI` jobs (Python, compose, **sandbox isolation**, TypeScript) | The sandbox job proves a sandbox cannot reach the privileged daemon. |
| | Repository admins may bypass **only through a pull request** | An emergency merge is possible but never a direct push. |
| Ruleset `release-tags` (`refs/tags/v*`) | Only admins can create, update or delete | Publishing an installer is an owner decision. |
| Ruleset `upstream-merge` | No deletion, no force push | Long-lived branch (decision L2). |
| Repository settings | Merge commits on; squash and rebase **off**; **delete branch on merge off** | Deleting the head branch on merge would delete `upstream-merge` itself. |
| Security | Secret scanning and push protection on; private vulnerability reporting on; workflow tokens read-only by default | Free for public repositories. |

Deliberately **not** enabled: required linear history (merge commits are not linear), required signatures, code-owner review.
`.github/CODEOWNERS` is upstream's and names `@NousResearch/hermes-agent-core`, a team that does not exist here; requiring code-owner
review would make every PR unmergeable. It is inert as long as the ruleset leaves that off.

## Apply (the repository owner does this)

1. Merge the PR that adds the `Sandbox cannot reach the Docker daemon` job first. Requiring a check that nothing reports would
   block every PR on `main`.
2. Sign in as the owner (`gh auth login`, account `sentio-one`) in a clone of this repository.
3. Preview, then apply and replace the two misconfigured rulesets:
   ```
   scripts/fork/apply-github-protection.sh
   scripts/fork/apply-github-protection.sh --apply --replace-legacy
   ```
   Or, without the script: Settings > Rules > Rulesets > New ruleset > Import a ruleset, once per file in `.github/rulesets/`.
4. Verify (anyone can run this, it is read-only): `scripts/fork/check-github-protection.sh` must end with `github protection: ok`.
   A weekly workflow (`protection-drift.yml`) runs the same check and goes red if the protection drifts.

The create and update calls in the apply script could not be tried by the author (no admin rights on the repository); the checker is
the proof. If GitHub rejects a ruleset, the error names the field.

## By hand (not expressible as a ruleset)

- **Two-factor authentication on both accounts** (`sentio-one` and `TheophilusChinomona`).
- **Settings > Actions > General > Fork pull request workflows**: require approval for all outside collaborators. `Fork CI` has no
  secrets, but a public repository still runs workflows from strangers' PRs.
- **Dokploy:** auto-deploy follows `main`; if Dokploy offers to wait for CI checks before deploying, turn that on.
- **Security contact:** `.github/SECURITY.md` sends reports for Sentio One's own code to the owner, not to Nous Research (upstream's
  `SECURITY.md` does). GitHub prefers `.github/SECURITY.md`, so upstream's file stays untouched and does not conflict on merges.

## Before making the repository private (decision L3)

Branch protection and rulesets are **free for public repositories**. As GitHub documents it (check the current wording on GitHub's
"About rulesets" page before relying on it), they need a **paid plan on a private repository** (GitHub Pro for a personal account, Team for an organization); on the Free plan, rules on a private repository are not
enforced. So the order is: confirm the plan at github.com/settings/billing, upgrade or move the repository to an organization, **then**
go private, then run the checker again. Going private first would silently drop all of the above.

A personal account also has no teams, so "second reviewer" means a second account. An organization (Team plan) would give teams,
proper reviewer groups and CODEOWNERS that work; worth considering when a second person joins.
