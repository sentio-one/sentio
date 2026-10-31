#!/usr/bin/env bash
# Tag HEAD with v<version> when that version has no tag yet. The version is the one in apps/desktop/package.json and it must equal
# pyproject.toml's. Pushing the tag (by a token that is allowed to) starts the release build in .github/workflows/release-light.yml.
#
#   scripts/fork/tag-release.sh                 # dry run: say what would happen
#   scripts/fork/tag-release.sh --push          # create the annotated tag and push it
#
# Idempotent: a version that already has a tag is left alone, so this can run on every merge that touches a version file.
# Refuses a version that is not plain semver, has a patch above 9, differs between the two files, or is not newer than the newest v* tag.
# Docs: docs/private-fork/RELEASING.md.
set -euo pipefail

cd "$(dirname "$0")/../.."
remote="${TAG_REMOTE:-origin}"
push=0
[ "${1:-}" = "--push" ] && push=1

desktop="$(python3 -c 'import json; print(json.load(open("apps/desktop/package.json"))["version"])')"
python_version="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -1)"
die() { echo "tag-release: $*" >&2; exit 1; }

[ "$desktop" = "$python_version" ] \
  || die "apps/desktop/package.json says $desktop but pyproject.toml says $python_version; bump both (scripts/fork/bump-version.sh)"
[[ "$desktop" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version '$desktop' is not plain MAJOR.MINOR.PATCH"
[[ "$desktop" =~ ^[0-9]+\.[0-9]+\.[0-9]$ ]] \
  || die "version '$desktop' has a patch above 9; after X.Y.9 the next release is X.(Y+1).0 (scripts/fork/bump-version.sh next)"
tag="v$desktop"

if git ls-remote --exit-code --tags "$remote" "refs/tags/$tag" >/dev/null 2>&1; then
  echo "tag-release: $tag already exists on $remote, nothing to do"
  exit 0
fi

# Only plain-semver tags count; a pre-release such as v0.3.0-rc1 does not block a later 0.2.0.
latest="$(git ls-remote --tags --refs "$remote" 'v*' | sed 's#.*refs/tags/v##' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1 || true)"
if [ -n "$latest" ] && [ "$(printf '%s\n%s\n' "$latest" "$desktop" | sort -V | tail -1)" != "$desktop" ]; then
  die "version $desktop is not newer than the latest tag v$latest"
fi

# Releases are cut from stable: a version bump that was merged to main but not promoted is not a release yet.
stable="${TAG_STABLE_BRANCH:-stable}"
git fetch --quiet "$remote" "+refs/heads/$stable:refs/remotes/$remote/$stable" \
  || die "cannot fetch $remote/$stable; releases are cut from the $stable branch (docs/private-fork/RELEASING.md)"
git merge-base --is-ancestor HEAD "refs/remotes/$remote/$stable" \
  || die "HEAD $(git rev-parse --short HEAD) is not on $remote/$stable; promote it first (scripts/fork/promote-stable.sh)"

if [ "$push" -eq 0 ]; then
  echo "tag-release: would tag $(git rev-parse --short HEAD) as $tag (latest tag: ${latest:+v}${latest:-none}); pass --push to do it"
  exit 0
fi
# An annotated tag needs a committer identity, and a CI runner has none configured.
git -c user.name="${TAG_AUTHOR_NAME:-github-actions[bot]}" \
    -c user.email="${TAG_AUTHOR_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}" \
    tag -a "$tag" -m "Sentio One Light desktop $tag" HEAD
git push "$remote" "refs/tags/$tag"
echo "tag-release: pushed $tag at $(git rev-parse --short HEAD)"
