#!/usr/bin/env bash
# Disable every GitHub Actions workflow that comes from upstream, on this repository.
#
# Upstream's workflows run on Blacksmith runners this organisation does not have, so they only queue. They stay in the tree
# (deleting them would make every upstream merge a modify/delete conflict); this switches them off on GitHub instead.
#
# A workflow is upstream's when its FILE exists in upstream/main, so the fork's own workflows (fork-ci.yml, release-light.yml,
# tag-release.yml, ...) are kept without a list to maintain. A workflow that arrives with an upstream merge is enabled by default:
# re-run this after each merge.
#
#   scripts/fork/disable-upstream-workflows.sh           # show what would change
#   scripts/fork/disable-upstream-workflows.sh --apply   # do it
set -euo pipefail

cd "$(dirname "$0")/../.."
# The fork is `origin`. Never `gh repo view`: with an `upstream` remote present, gh may resolve to NousResearch/hermes-agent.
fork_repo() { git remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##'; }
repo="${HERMES_FORK_REPO:-$(fork_repo)}"
apply=0
[ "${1:-}" = "--apply" ] && apply=1

git fetch --quiet upstream main 2>/dev/null || echo "warning: could not fetch upstream; using the upstream/main already here" >&2
if ! git rev-parse --verify --quiet refs/remotes/upstream/main >/dev/null; then
  echo "no upstream/main to compare with. Add it: git remote add upstream https://github.com/NousResearch/hermes-agent.git && git fetch upstream main" >&2
  exit 1
fi
upstream_files="$(git ls-tree -r --name-only refs/remotes/upstream/main -- .github/workflows)"
echo "repository: $repo ($([ "$apply" -eq 1 ] && echo APPLYING || echo "dry run, nothing will change"))"

gh api "repos/$repo/actions/workflows" --paginate --jq '.workflows[] | [.id, .state, .path, .name] | @tsv' |
while IFS=$'\t' read -r id state path name; do
  [ "$state" = "active" ] || continue
  grep -qxF "$path" <<<"$upstream_files" || continue   # the fork's own workflow
  if [ "$apply" -eq 1 ]; then
    gh workflow disable "$id" -R "$repo" && echo "disabled: $name ($path)"
  else
    echo "would disable: $name ($path)"
  fi
done
