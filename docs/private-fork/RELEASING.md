# Releasing the Light desktop client

A release is a **version bump that gets merged**. Nothing is tagged by hand and nothing is published by a person.

```
scripts/fork/bump-version.sh 0.2.0      one command, four files
        |
   pull request  ->  merge to main
        |
   tag-release.yml   sees a version file changed, creates tag v0.2.0 (with RELEASE_TOKEN)
        |
   release-light.yml  the tag push builds HermesLight-0.2.0-win-x64.msix and publishes the GitHub release
```

Every other merge to `main` (no version change) still builds the installer, kept as a workflow artifact for 30 days and not
published. Docs-only merges skip the build.

## One-time setup (the repository owner, account `sentio-one`)

The tag must be pushed by a token that is allowed to create `v*` tags and that starts other workflows. The built-in `GITHUB_TOKEN`
can do neither: GitHub does not start workflows from tags that token pushed, and once the protection in `REPO_PROTECTION.md` is applied
the `release-tags` ruleset only lets repository admins create `v*` tags. So the workflow uses a token you create.

1. Sign in to GitHub as **`sentio-one`** (the owner, not `TheophilusChinomona`; only an admin can create release tags).
2. Profile picture > **Settings** > **Developer settings** > **Personal access tokens** > **Fine-grained tokens** > **Generate new token**.
3. Fill in:
   - **Token name:** `sentio-release-tagger`
   - **Expiration:** as long as GitHub allows (up to about a year for a fine-grained token). Put the expiry date in your calendar.
   - **Resource owner:** `sentio-one`
   - **Repository access:** *Only select repositories* > `sentio-one/sentio`
   - **Permissions** > *Repository permissions* > **Contents: Read and write**. Nothing else (Metadata: read-only is added for you).
4. **Generate token** and copy it (GitHub shows it once).
5. In the repository: **Settings** > **Secrets and variables** > **Actions** > **New repository secret**. Name it exactly
   **`RELEASE_TOKEN`**, paste the token, **Add secret**.
6. Check it works without creating anything: **Actions** > **Tag release** > **Run workflow** (branch `main`). It should finish green
   and say `tag-release: v0.1.0 already exists on origin, nothing to do`. A red run with
   `RELEASE_TOKEN is not set` means step 5 was missed; a checkout or push error means the token lacks Contents: write on this repository.

## Cutting a release

```
scripts/fork/bump-version.sh 0.2.0     # package.json, pyproject.toml, package-lock.json, uv.lock (runs `uv lock`)
git switch -c release-0.2.0 && git commit -am "chore(release): 0.2.0" && git push -u origin release-0.2.0
gh pr create --base main               # review, merge with a merge commit
```

Then watch **Actions**: `Tag release` (seconds) then `Release Light Desktop` (about 7 minutes). The release appears under **Releases**
as `HermesLight-0.2.0-win-x64.msix` plus its `.sha256`. The installer is unsigned (see the release notes for the install step).

The tagger refuses, and says why, when the two version files disagree, the version is not plain `MAJOR.MINOR.PATCH`, or it is not
newer than the newest existing `v*` tag. It is safe to re-run: a version that already has a tag is left alone.

To tag by hand instead (as the owner): `git tag -a v0.2.0 -m "v0.2.0" <merge-commit> && git push origin v0.2.0`.

## What the token can do, and keeping it safe

- It can write to this repository's contents as the owner, but it is scoped to this one repository and one permission, and it only
  appears in the `Tag release` workflow, which runs for pushes to `main` (reviewed code) and manual runs on `main`. It is not available
  to pull-request workflows.
- Anyone who can merge to `main` can change that workflow. With the protection from `REPO_PROTECTION.md` applied (pull request,
  review, required checks) that takes a reviewed change.
- **Revoke it** (Settings > Developer settings > the token > Delete) if it may have leaked, then create a new one and replace the secret.
- **When it expires** the `Tag release` workflow fails with a clear error on the next version bump. Create a new token (steps 2 to 5,
  same name is fine), then **Actions** > **Tag release** > **Run workflow** to tag the version that was missed.
- If the repository moves to an organization, create the token again there; a personal token does not follow it.
