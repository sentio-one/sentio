#!/usr/bin/env bash
# Disable every GitHub Actions workflow on the fork except the fork's own.
#
# Upstream's workflows run on Blacksmith runners this organisation does not have, so they only queue. They stay in
# the tree (deleting them would make every upstream merge a modify/delete conflict); this switches them off on
# GitHub instead. A workflow that arrives with an upstream merge is enabled by default, so re-run this after each merge.
#
#   scripts/fork/disable-upstream-workflows.sh           # show what would change
#   scripts/fork/disable-upstream-workflows.sh --apply   # do it
set -euo pipefail

KEEP=("Fork CI" "Release Light Desktop")
repo="${HERMES_FORK_REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
apply=0
[[ "${1:-}" == "--apply" ]] && apply=1

while IFS=$'\t' read -r name state id; do
  for keep in "${KEEP[@]}"; do
    [[ "$name" == "$keep" ]] && continue 2
  done
  [[ "$state" == "active" ]] || continue
  if (( apply )); then
    gh workflow disable "$id" -R "$repo" && echo "disabled: $name"
  else
    echo "would disable: $name"
  fi
done < <(gh workflow list -R "$repo" --all -L 200)
