#!/usr/bin/env bash
# Open the pull request that promotes main to stable. It never pushes to stable: stable only moves through a reviewed PR
# (.github/rulesets/stable.json). Merging that PR redeploys every client Dokploy project that follows stable and, when the
# promotion carries a version bump, tags and publishes a release (.github/workflows/tag-release.yml).
#
#   scripts/fork/promote-stable.sh              # open the PR, or say there is nothing to promote
#   scripts/fork/promote-stable.sh --dry-run    # print the title and body only
# Docs: docs/private-fork/RELEASING.md.
set -euo pipefail

cd "$(dirname "$0")/../.."
remote="${PROMOTE_REMOTE:-origin}"
dry=0
[ "${1:-}" = "--dry-run" ] && dry=1

git fetch --quiet "$remote" main stable
count="$(git rev-list --count "$remote/stable..$remote/main")"
if [ "$count" -eq 0 ]; then
  echo "promote-stable: stable already contains everything on main"
  exit 0
fi

version_of() { git show "$remote/$1:apps/desktop/package.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'; }
new="$(version_of main)"
old="$(version_of stable)"
if [ "$new" = "$old" ]; then
  release="Same version ($new): nothing will be tagged or released, but client backends will redeploy."
else
  release="Version $old -> $new: merging tags v$new and publishes the installer."
fi
log="$(git log --no-merges --format='- %s (%h)' "$remote/stable..$remote/main")"
title="Promote main to stable ($count commits, $new)"
body="$release

Commits being promoted:
$log

Merging redeploys every client Dokploy project that follows stable."

if [ "$dry" -eq 1 ]; then
  printf '%s\n\n%s\n' "$title" "$body"
  exit 0
fi
gh pr create --base stable --head main --title "$title" --body "$body"
