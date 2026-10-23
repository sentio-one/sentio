#!/usr/bin/env bash
# Replace the rolling `main-latest` pre-release with the installer just built from main.
#
# Why a release and not just the workflow artifact: an artifact needs a GitHub login, expires after 30 days, and announces nothing.
# A release has one stable address, keeps the installer, and a freshly published one notifies everyone watching releases. It is a
# PRE-release and never marked Latest, so "Latest" stays the newest real version (a v* tag, see docs/private-fork/RELEASING.md).
#
# The release is deleted and created again (not edited) so it carries the new commit, the new date, and a new release event.
# Needs: gh authenticated with contents: write (GITHUB_TOKEN), GH_REPO, GITHUB_SHA, and the installer in apps/desktop/release.
set -euo pipefail

tag="${ROLLING_TAG:-main-latest}"
dir="${RELEASE_DIR:-apps/desktop/release}"
sha="${GITHUB_SHA:?GITHUB_SHA is not set}"
repo="${GH_REPO:?GH_REPO is not set}"
short="${sha:0:7}"

shopt -s nullglob
installers=("$dir"/HermesLight-*.msix)
checksums=("$dir"/HermesLight-*.msix.sha256)
[ "${#installers[@]}" -eq 1 ] && [ "${#checksums[@]}" -eq 1 ] \
  || { echo "refresh-main-release: expected one installer and one checksum in $dir, found ${#installers[@]} and ${#checksums[@]}" >&2; exit 1; }

subject="$(gh api "repos/$repo/commits/$sha" --jq '.commit.message' | head -1)"
hash="$(cut -d' ' -f1 "${checksums[0]}")"
notes="Newest build of \`main\`, commit [$short](https://github.com/$repo/commit/$sha): $subject

Built $(date -u '+%Y-%m-%d %H:%MZ'). SHA-256 of the installer: \`$hash\`

**This is a pre-release build of the in-progress main branch, not a stable release.** Stable releases are the version tags; the one marked Latest is the newest of those. This release is replaced on every merge to main.

The installer is unsigned: turn on Windows Developer Mode, or run \`Add-AppxPackage -Path .\\HermesLight-*.msix -AllowUnsigned\`."

gh release delete "$tag" --cleanup-tag --yes >/dev/null 2>&1 || true   # none yet, the first time
gh release create "$tag" "${installers[0]}" "${checksums[0]}" \
  --target "$sha" --prerelease --latest=false \
  --title "Light desktop, newest main build ($short)" --notes "$notes"
echo "refresh-main-release: $tag now points at $short"
