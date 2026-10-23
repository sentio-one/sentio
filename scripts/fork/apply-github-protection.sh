#!/usr/bin/env bash
# Apply the repository protection described by .github/rulesets/*.json. Run it as the REPOSITORY OWNER (an admin): rulesets,
# merge settings and security features need admin rights that a write collaborator does not have.
#
#   scripts/fork/apply-github-protection.sh                   # show what would change (read-only)
#   scripts/fork/apply-github-protection.sh --apply           # create/update the rulesets and settings
#   scripts/fork/apply-github-protection.sh --apply --replace-legacy
#                                                             # also delete rulesets that target a branch that does not exist
# Afterwards: scripts/fork/check-github-protection.sh (anyone can run it).
#
# Order matters: apply this AFTER the PR that adds the `Sandbox cannot reach the Docker daemon` job has merged, or `main`
# will wait forever for a check that never reports. Docs: docs/private-fork/REPO_PROTECTION.md.
set -euo pipefail

cd "$(dirname "$0")/../.."
# The fork is `origin`. Never `gh repo view`: with an `upstream` remote present, gh may resolve to NousResearch/hermes-agent.
fork_repo() { git remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##'; }
repo="${HERMES_FORK_REPO:-$(fork_repo)}"
apply=0; replace_legacy=0
for arg in "$@"; do
  case "$arg" in
    --apply) apply=1 ;;
    --replace-legacy) replace_legacy=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
echo "repository: $repo ($([ "$apply" -eq 1 ] && echo APPLYING || echo "dry run, nothing will change"))"

if [ "$(gh api "repos/$repo" --jq .permissions.admin)" != "true" ]; then
  echo "The authenticated GitHub account ($(gh api user --jq .login)) is not an admin of $repo." >&2
  echo "Run this as the repository owner (gh auth login), or apply the same rulesets by hand: Settings > Rules > Rulesets > Import a ruleset." >&2
  exit 1
fi

run() { if [ "$apply" -eq 1 ]; then "$@"; else echo "would run: $*"; fi; }

for file in .github/rulesets/*.json; do
  name="$(jq -r .name "$file")"
  id="$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name==\"$name\") | .id")"
  if [ -n "$id" ]; then
    echo "update ruleset '$name' (id $id) from $file"
    run gh api -X PUT "repos/$repo/rulesets/$id" --input "$file" --silent
  else
    echo "create ruleset '$name' from $file"
    run gh api -X POST "repos/$repo/rulesets" --input "$file" --silent
  fi
done

# A ruleset that names one exact branch that does not exist protects nothing (the original two did: refs/heads/protect-main).
for id in $(gh api "repos/$repo/rulesets" --jq '.[].id'); do
  detail="$(gh api "repos/$repo/rulesets/$id")"
  [ "$(jq -r .target <<<"$detail")" = branch ] || continue
  for ref in $(jq -r '.conditions.ref_name.include[]? | select(startswith("refs/heads/") and (contains("*")|not))' <<<"$detail"); do
    if ! gh api "repos/$repo/branches/${ref#refs/heads/}" >/dev/null 2>&1; then
      echo "ruleset '$(jq -r .name <<<"$detail")' (id $id) targets $ref, which does not exist"
      [ "$replace_legacy" -eq 1 ] && run gh api -X DELETE "repos/$repo/rulesets/$id" --silent || echo "  (pass --replace-legacy to delete it)"
    fi
  done
done

echo "repository merge settings: merge commits only, keep branches (upstream-merge is long-lived)"
run gh api -X PATCH "repos/$repo" -F allow_merge_commit=true -F allow_squash_merge=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=false --silent

echo "secret scanning with push protection, private vulnerability reporting, read-only workflow tokens"
run gh api -X PATCH "repos/$repo" --input - --silent <<'JSON'
{"security_and_analysis": {"secret_scanning": {"status": "enabled"}, "secret_scanning_push_protection": {"status": "enabled"}}}
JSON
run gh api -X PUT "repos/$repo/private-vulnerability-reporting" --silent
run gh api -X PUT "repos/$repo/actions/permissions/workflow" -f default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=false --silent

echo "done. verify with: scripts/fork/check-github-protection.sh"
