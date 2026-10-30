#!/usr/bin/env bash
# Read-only check that the repository's protection matches what .github/rulesets/*.json describe. Needs no admin rights:
# rulesets of a public repository are readable by anyone. Exits 1 on any gap, so CI can run it on a schedule to catch drift.
#
#   scripts/fork/check-github-protection.sh
#
# Why this exists: the first rulesets on this repository targeted `refs/heads/protect-main`, a branch that does not exist,
# so `main` was unprotected while two "active" rulesets looked reassuring. This looks at what is actually enforced.
set -euo pipefail

cd "$(dirname "$0")/../.."
# The fork is `origin`. Never `gh repo view`: with an `upstream` remote present, gh may resolve to NousResearch/hermes-agent.
fork_repo() { git remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##'; }
repo="${HERMES_FORK_REPO:-$(fork_repo)}"
echo "repository: $repo"
failed=0
ok()   { echo "ok    $1"; }
fail() { echo "FAIL  $1"; failed=1; }
warn() { echo "warn  $1"; }

rulesets="$(
  for id in $(gh api "repos/$repo/rulesets" --jq '.[].id'); do gh api "repos/$repo/rulesets/$id"; done | jq -s '.'
)"
[ "$(jq 'length' <<<"$rulesets")" -gt 0 ] || rulesets='[]'

# active rulesets of a target whose include list names one of the given refs, as a JSON array
covering() {  # covering <target> <ref>...
  local target=$1; shift
  jq --arg t "$target" --argjson refs "$(printf '%s\n' "$@" | jq -R . | jq -s .)" \
    '[.[] | select(.enforcement=="active" and .target==$t)
          | select([.conditions.ref_name.include[]?] | any(. as $i | $refs | index($i)))]' <<<"$rulesets"
}
has_rule() { jq -e --arg r "$1" 'any(.[]; any(.rules[]; .type==$r))' >/dev/null; }

default_branch="$(gh api "repos/$repo" --jq .default_branch)"
# check_branch <label> <ruleset file> <active rulesets covering the branch, as JSON>: the four rules, merge commits only,
# and every status check the ruleset file names.
check_branch() {
  local label=$1 file=$2 rs=$3 methods live need ctx
  if [ "$(jq length <<<"$rs")" -eq 0 ]; then
    fail "no active ruleset covers $label (a ruleset must name the branch)"
    return
  fi
  for rule in deletion non_fast_forward pull_request required_status_checks; do
    has_rule "$rule" <<<"$rs" && ok "$label: $rule" || fail "$label: missing the $rule rule"
  done
  methods="$(jq -c '[.[].rules[] | select(.type=="pull_request") | .parameters.allowed_merge_methods] | first // []' <<<"$rs")"
  [ "$methods" = '["merge"]' ] && ok "$label: only merge commits are allowed (upstream history stays intact)"     || fail "$label: allowed merge methods are $methods, must be [\"merge\"] (a squash drops upstream's ancestry)"
  live="$(jq -r '[.[].rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context] | .[]' <<<"$rs")"
  for need in $(jq -r '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context | gsub(" ";"_")' "$file"); do
    ctx="${need//_/ }"
    grep -qxF "$ctx" <<<"$live" && ok "$label: required check: $ctx" || fail "$label: required check missing: $ctx"
  done
}

check_branch "$default_branch" .github/rulesets/main.json "$(covering branch '~DEFAULT_BRANCH' "refs/heads/$default_branch" '~ALL')"
check_branch stable .github/rulesets/stable.json "$(covering branch refs/heads/stable)"

tags_rs="$(covering tag 'refs/tags/v*')"
if [ "$(jq length <<<"$tags_rs")" -gt 0 ] && has_rule creation <<<"$tags_rs"; then
  ok "v* release tags: creation is restricted (a pushed v* tag publishes the installer)"
else
  fail "v* tags are not protected: anyone with write access can publish a release by pushing a tag"
fi

um_rs="$(covering branch refs/heads/upstream-merge)"
if has_rule deletion <<<"$um_rs" && has_rule non_fast_forward <<<"$um_rs"; then ok "upstream-merge: no deletion, no force push"
else fail "upstream-merge branch is not protected from deletion and force pushes"; fi

# a ruleset naming one exact branch that does not exist protects nothing
while IFS= read -r ref; do
  [ -n "$ref" ] || continue
  gh api "repos/$repo/branches/${ref#refs/heads/}" >/dev/null 2>&1 \
    || fail "a ruleset targets $ref, which does not exist, so it protects nothing"
done < <(jq -r '.[] | select(.target=="branch") | .conditions.ref_name.include[]? | select(startswith("refs/heads/") and (contains("*")|not))' <<<"$rulesets")

for flag in allow_squash_merge allow_rebase_merge; do
  value="$(gh api "repos/$repo" --jq ".$flag")"
  case "$value" in
    false) ok "repository setting $flag is off" ;;
    true)  fail "repository setting $flag is on" ;;
    *)     warn "cannot read $flag (needs push access)" ;;
  esac
done

if [ "$failed" -ne 0 ]; then echo "github protection: GAPS FOUND (apply with scripts/fork/apply-github-protection.sh as the repository owner)" >&2; exit 1; fi
echo "github protection: ok"
