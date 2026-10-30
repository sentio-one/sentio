# Stable Branch and 0.3.x Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (this is a small-to-medium plan: five code tasks, one docs task, one maintainer rollout). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a long-lived `stable` branch that is promoted from `main` by reviewed PR, cut Light desktop releases from it with a `0.3.0, 0.3.1 … 0.3.9, 0.4.0` version rule, and document how every client Dokploy project follows it.

**Architecture:** Bash scripts under `scripts/fork/` already own versioning (`bump-version.sh`, `tag-release.sh`) and repository protection (`apply-/check-github-protection.sh`, driven by `.github/rulesets/*.json`). This plan extends them (version rule, "commit must be on stable" guard, a `stable` ruleset the checker verifies), adds one new script (`promote-stable.sh`), moves the `Tag release` workflow trigger from `main` to `stable`, and updates the docs. `release-light.yml` is unchanged: it still builds on pushes to `main` (rolling `main-latest`) and publishes on `v*` tags.

**Tech Stack:** bash, `jq`, `gh`, GitHub Actions, GitHub rulesets, pytest (`tests/scripts/`, `@pytest.mark.platforms("posix")`).

**Spec:** `docs/private-fork/2026-10-30-stable-branch-design.md`

## Global Constraints

- Patch is a single digit: `0.3.0 … 0.3.9`, then `0.4.0`. `0.3.9` is followed by `0.4.0`. First stable release is `0.3.0`.
- Release tags are cut from `stable` only. `main` keeps building the rolling `main-latest` pre-release; builds from `main` keep the last released version.
- `stable` is protected like `main`: pull request, 1 approval, merge commits only, the same four required checks, no deletion, no force push.
- `stable` only ever moves through a PR (`main` into `stable`). Hotfixes land on `main` first, then are cherry-picked into `stable`.
- Maintainer-only (repository admin) actions are documented as steps, never scripted into CI: create the branch, apply the ruleset, run `Tag release` once, repoint Dokploy.
- Client self-update is out of scope; testers reinstall the unsigned NSIS `.exe` from the release.
- Tests that run bash scripts are `@pytest.mark.platforms("posix")`. They **skip on Windows**; run them on Linux/WSL, or read the result from the `Access, invites and deployment (Python)` job of `Fork CI` after pushing. Always run tests with `scripts/run_tests.sh`, never bare `pytest`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/fork/bump-version.sh` (modify) | Compute/accept the next version; enforce the single-digit patch rule |
| `scripts/fork/tag-release.sh` (modify) | Refuse patch above 9; refuse a commit that is not on `origin/stable` |
| `tests/scripts/test_release_version_scripts.py` (modify) | Cover both of the above |
| `.github/workflows/tag-release.yml` (modify) | Trigger on `stable`, full history for the ancestry check |
| `.github/rulesets/stable.json` (create) | The `stable` ruleset, applied by the existing apply script |
| `scripts/fork/check-github-protection.sh` (modify) | Verify `stable` the same way as `main` |
| `tests/scripts/test_check_github_protection.py` (modify) | Cover the `stable` checks |
| `scripts/fork/promote-stable.sh` (create) | Open the `main` into `stable` promotion PR |
| `tests/scripts/test_promote_stable.py` (create) | Cover the promote script |
| `docs/private-fork/{RELEASING,REPO_PROTECTION,FORK_DECISIONS}.md`, `deploy/dokploy/README.md` (modify) | Document the new flow |

Work happens on branch `stable-branch-design` (it already carries the spec). One PR into `main` at the end.

---

### Task 1: Version rule in bump-version.sh and tag-release.sh

**Files:**
- Modify: `scripts/fork/bump-version.sh:10-19`
- Modify: `scripts/fork/tag-release.sh:24`
- Test: `tests/scripts/test_release_version_scripts.py`

**Interfaces:**
- Produces: `bump-version.sh [next|MAJOR.MINOR.PATCH]` (no argument means `next`); both scripts reject a patch above 9.

- [ ] **Step 1: Write the failing tests**

Append to `tests/scripts/test_release_version_scripts.py`:

```python
LOCK = ('{\n  "packages": {\n    "apps/desktop": {\n      "name": "hermes",\n      "version": "0.0.0",\n'
        '      "dependencies": {}\n    }\n  }\n}\n')


def _bump(work, *args):
    (work / "package-lock.json").write_text(LOCK, encoding="utf-8")
    return subprocess.run(["bash", "scripts/fork/bump-version.sh", *args], cwd=work, capture_output=True, text=True,
                          check=False, env={"PATH": "/usr/bin:/bin"})


@pytest.mark.parametrize("current,expected", [
    ("0.3.0", "0.3.1"), ("0.3.8", "0.3.9"), ("0.3.9", "0.4.0"), ("0.9.9", "0.10.0"),
])
def test_next_counts_the_patch_from_0_to_9_then_rolls_the_minor(clone, current, expected):
    _write_versions(clone, current)
    for args in ((), ("next",)):
        _write_versions(clone, current)
        done = _bump(clone, *args)
        assert done.returncode == 0, done.stdout + done.stderr
        assert json.loads((clone / "apps/desktop/package.json").read_text())["version"] == expected
        assert re.search(rf'^version = "{re.escape(expected)}"$', (clone / "pyproject.toml").read_text(), re.MULTILINE)


def test_bump_refuses_a_patch_above_9(clone):
    done = _bump(clone, "0.3.10")
    assert done.returncode != 0 and "single digit" in done.stdout + done.stderr


def test_the_tagger_refuses_a_patch_above_9(clone):
    _bump_commit(clone, "0.2.10")
    code, out = _tagger(clone, "--push")
    assert code == 1 and "patch above 9" in out, out
    assert _remote_tags(clone) == ["v0.1.0"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run_tests.sh tests/scripts/test_release_version_scripts.py -v` on Linux/WSL.
Expected: the new tests FAIL (`next` is rejected as "usage", `0.3.10` is accepted, the tagger tags `v0.2.10`). On Windows they skip; push the test-only commit and read the failure in the `Access, invites and deployment (Python)` job instead.

- [ ] **Step 3: Implement in bump-version.sh**

Replace the usage header comment line `#   scripts/fork/bump-version.sh 0.2.0` with:

```bash
#   scripts/fork/bump-version.sh next       # the next version: 0.3.0 -> 0.3.1 ... 0.3.9 -> 0.4.0 (also the default with no argument)
#   scripts/fork/bump-version.sh 0.3.0      # or an explicit version (patch is a single digit)
```

Replace lines 10-19 (`cd "$(dirname ...` through the closing `fi` of the "not newer" check) with:

```bash
cd "$(dirname "$0")/../.."
arg="${1:-next}"
current="$(python3 -c 'import json; print(json.load(open("apps/desktop/package.json"))["version"])')"
if [ "$arg" = next ]; then
  IFS=. read -r major minor patch <<<"$current"
  [[ "$patch" =~ ^[0-9]+$ ]] || { echo "bump-version: cannot compute the next version from '$current'" >&2; exit 2; }
  if [ "$patch" -lt 9 ]; then new="$major.$minor.$((patch + 1))"; else new="$major.$((minor + 1)).0"; fi
else
  new="$arg"
fi
[[ "$new" =~ ^[0-9]+\.[0-9]+\.[0-9]$ ]] \
  || { echo "usage: $0 [next|MAJOR.MINOR.PATCH] (the patch is a single digit: 0.3.9 is followed by 0.4.0)" >&2; exit 2; }
if [ "$(printf '%s\n%s\n' "$current" "$new" | sort -V | tail -1)" != "$new" ] || [ "$current" = "$new" ]; then
  echo "bump-version: $new is not newer than the current version $current" >&2
  exit 1
fi
```

- [ ] **Step 4: Implement in tag-release.sh**

After the line `[[ "$desktop" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version '$desktop' is not plain MAJOR.MINOR.PATCH"` add:

```bash
[[ "$desktop" =~ ^[0-9]+\.[0-9]+\.[0-9]$ ]] \
  || die "version '$desktop' has a patch above 9; after X.Y.9 the next release is X.(Y+1).0 (scripts/fork/bump-version.sh next)"
```

Also extend the header comment line `# Refuses a version that is not plain semver, ...` to mention "has a patch above 9".

- [ ] **Step 5: Run the tests to verify they pass**

Run: `scripts/run_tests.sh tests/scripts/test_release_version_scripts.py -v` (Linux/WSL, or via CI).
Expected: all tests PASS, including the pre-existing ones.

- [ ] **Step 6: Commit**

```bash
git add scripts/fork/bump-version.sh scripts/fork/tag-release.sh tests/scripts/test_release_version_scripts.py
git commit -m "Enforce the 0.3.x single-digit patch rule and add bump-version next

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: tag-release.sh refuses a commit that is not on stable

**Files:**
- Modify: `scripts/fork/tag-release.sh` (after the "newer than the latest tag" check)
- Modify: `tests/scripts/test_release_version_scripts.py` (fixture and `_bump_commit`)

**Interfaces:**
- Consumes: Task 1's `tag-release.sh`.
- Produces: `TAG_STABLE_BRANCH` env var (default `stable`); tagging requires `HEAD` to be an ancestor of `$remote/$TAG_STABLE_BRANCH`.

- [ ] **Step 1: Update the fixture and write the failing test**

In `clone`, change `_git(work, "push", "-q", "origin", "main", "v0.1.0")` to:

```python
    _git(work, "push", "-q", "origin", "main", "main:stable", "v0.1.0")
```

In `_bump_commit`, change `_git(work, "push", "-q", "origin", "main")` to:

```python
    _git(work, "push", "-q", "origin", "main", "main:stable")
```

Append:

```python
def test_it_refuses_a_commit_that_has_not_been_promoted_to_stable(clone):
    _write_versions(clone, "0.2.0")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "bump on main only")
    _git(clone, "push", "-q", "origin", "main")  # main moves, stable does not
    code, out = _tagger(clone, "--push")
    assert code == 1 and "is not on origin/stable" in out, out
    assert _remote_tags(clone) == ["v0.1.0"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `scripts/run_tests.sh tests/scripts/test_release_version_scripts.py -k promoted -v`
Expected: FAIL (the tagger tags `v0.2.0` from an unpromoted commit).

- [ ] **Step 3: Implement**

In `tag-release.sh`, immediately after the `latest` check (the `if [ -n "$latest" ] ... fi` block) and before `if [ "$push" -eq 0 ]`, add:

```bash
# Releases are cut from stable: a version bump that was merged to main but not promoted is not a release yet.
stable="${TAG_STABLE_BRANCH:-stable}"
git fetch --quiet "$remote" "+refs/heads/$stable:refs/remotes/$remote/$stable" \
  || die "cannot fetch $remote/$stable; releases are cut from the $stable branch (docs/private-fork/RELEASING.md)"
git merge-base --is-ancestor HEAD "refs/remotes/$remote/$stable" \
  || die "HEAD $(git rev-parse --short HEAD) is not on $remote/$stable; promote it first (scripts/fork/promote-stable.sh)"
```

- [ ] **Step 4: Run to verify the whole file passes**

Run: `scripts/run_tests.sh tests/scripts/test_release_version_scripts.py -v`
Expected: all PASS (the existing tests now tag from commits that were pushed to `stable` too).

- [ ] **Step 5: Commit**

```bash
git add scripts/fork/tag-release.sh tests/scripts/test_release_version_scripts.py
git commit -m "Only tag a release from a commit that is on stable

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Tag release workflow follows stable

**Files:**
- Modify: `.github/workflows/tag-release.yml`

**Interfaces:**
- Consumes: Task 2's ancestry guard (needs full history to compare `HEAD` with `origin/stable`).

- [ ] **Step 1: Edit the workflow**

Change the header comment's first line to `# Tags the release when a merge to stable bumps the version (scripts/fork/tag-release.sh). ...`.

Change the trigger:

```yaml
on:
  push:
    branches: [stable]
    paths:
      - 'apps/desktop/package.json'
      - 'pyproject.toml'
  workflow_dispatch:
```

Change the job guard `if: github.ref == 'refs/heads/main'` to `if: github.ref == 'refs/heads/stable'`.

Change the checkout step to fetch full history:

```yaml
      - uses: actions/checkout@f548e57e544e1ff5a4c46bf1e1b8685f8e4a348a # v7.0.1
        with:
          ref: ${{ github.sha }}
          fetch-depth: 0
          token: ${{ secrets.RELEASE_TOKEN }}
```

- [ ] **Step 2: Verify the file**

Run: `python -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/tag-release.yml')); print(d[True]['push']['branches'], d['jobs']['tag']['if'])"`
Expected: `['stable'] github.ref == 'refs/heads/stable'`

Run: `grep -n "main" .github/workflows/tag-release.yml`
Expected: no remaining reference that means the trigger branch (comments about `main-latest` are fine).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/tag-release.yml
git commit -m "Tag releases from stable instead of main

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The stable ruleset and its checker

**Files:**
- Create: `.github/rulesets/stable.json`
- Modify: `scripts/fork/check-github-protection.sh:33-49`
- Modify: `tests/scripts/test_check_github_protection.py`

**Interfaces:**
- Consumes: the existing `covering`, `has_rule`, `ok`, `fail` helpers in the checker. `apply-github-protection.sh` already applies every `.github/rulesets/*.json`, so it needs no change.
- Produces: ruleset named `stable` covering `refs/heads/stable`.

- [ ] **Step 1: Write the failing tests**

In `tests/scripts/test_check_github_protection.py`: in `_protected_state`, change `"branches": ["main", "upstream-merge"]` to `"branches": ["main", "stable", "upstream-merge"]`. Add:

```python
def _disable_stable(state):
    _ruleset(state, "stable")["enforcement"] = "disabled"


def _allow_squash_on_stable(state):
    rule = next(r for r in _ruleset(state, "stable")["rules"] if r["type"] == "pull_request")
    rule["parameters"]["allowed_merge_methods"] = ["merge", "squash"]
```

Add two rows to the existing `@pytest.mark.parametrize` list and ids:

```python
    (_disable_stable, "no active ruleset covers stable"),
    (_allow_squash_on_stable, "stable: allowed merge methods are"),
```
ids: `"stable-disabled"`, `"stable-squash-allowed"`.

- [ ] **Step 2: Run to verify they fail**

Run: `scripts/run_tests.sh tests/scripts/test_check_github_protection.py -v`
Expected: `test_the_committed_rulesets_satisfy_the_checker` PASSES; the two new rows FAIL (`_ruleset(state, "stable")` raises `StopIteration` because `stable.json` does not exist yet).

- [ ] **Step 3: Create the ruleset**

Create `.github/rulesets/stable.json` as a copy of `main.json` with two changes: `"name": "stable"` and `"include": ["refs/heads/stable"]`:

```json
{
  "name": "stable",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/stable"], "exclude": [] } },
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request" }
  ],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false,
        "allowed_merge_methods": ["merge"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          { "context": "Access, invites and deployment (Python)", "integration_id": 15368 },
          { "context": "Dokploy compose is valid", "integration_id": 15368 },
          { "context": "Sandbox cannot reach the Docker daemon", "integration_id": 15368 },
          { "context": "Access page and Light desktop (TypeScript)", "integration_id": 15368 }
        ]
      }
    }
  ]
}
```

- [ ] **Step 4: Generalise the checker**

In `check-github-protection.sh`, replace the block from `main_rs="$(covering branch ...` through its closing `fi` (the `if [ "$(jq length <<<"$main_rs")" -eq 0 ]; then ... fi` block) with:

```bash
# check_branch <label> <ruleset file> <active rulesets covering the branch as JSON>: the four rules, merge commits only,
# and every status check named in the ruleset file.
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
  [ "$methods" = '["merge"]' ] && ok "$label: only merge commits are allowed (upstream history stays intact)" \
    || fail "$label: allowed merge methods are $methods, must be [\"merge\"] (a squash drops upstream's ancestry)"
  live="$(jq -r '[.[].rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context] | .[]' <<<"$rs")"
  for need in $(jq -r '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context | gsub(" ";"_")' "$file"); do
    ctx="${need//_/ }"
    grep -qxF "$ctx" <<<"$live" && ok "$label: required check: $ctx" || fail "$label: required check missing: $ctx"
  done
}

check_branch "$default_branch" .github/rulesets/main.json "$(covering branch '~DEFAULT_BRANCH' "refs/heads/$default_branch" '~ALL')"
check_branch stable .github/rulesets/stable.json "$(covering branch refs/heads/stable)"
```

(Keep the existing `default_branch="$(gh api ...)"` line above it. The messages keep the substrings the existing tests assert: `no active ruleset covers main`, `required check missing: Sandbox ...`, `allowed merge methods are [...]`.)

- [ ] **Step 5: Run to verify everything passes**

Run: `scripts/run_tests.sh tests/scripts/test_check_github_protection.py -v`
Expected: all PASS, including the two new rows and the pre-existing "does not exist" and squash cases.

- [ ] **Step 6: Commit**

```bash
git add .github/rulesets/stable.json scripts/fork/check-github-protection.sh tests/scripts/test_check_github_protection.py
git commit -m "Protect stable like main and verify it in the weekly drift check

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: promote-stable.sh

**Files:**
- Create: `scripts/fork/promote-stable.sh` (mode 755)
- Create: `tests/scripts/test_promote_stable.py`

**Interfaces:**
- Produces: `scripts/fork/promote-stable.sh [--dry-run]`; env `PROMOTE_REMOTE` (default `origin`). Opens a PR `--base stable --head main`; `--dry-run` prints the title and body instead.

- [ ] **Step 1: Write the failing test**

Create `tests/scripts/test_promote_stable.py`:

```python
"""scripts/fork/promote-stable.sh against real git repositories and a fake `gh` that records its arguments."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "fork" / "promote-stable.sh"

pytestmark = pytest.mark.platforms("posix")

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
FAKE_GH = '#!{python}\nimport json, os, sys\nopen(os.environ["GH_CALLS"], "a").write(json.dumps(sys.argv[1:]) + "\\n")\n'


def _git(cwd, *args):
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _commit(work, version, message):
    (work / "apps" / "desktop").mkdir(parents=True, exist_ok=True)
    (work / "apps" / "desktop" / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (work / "note.txt").write_text(message, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", message)


@pytest.fixture
def work(tmp_path):
    """A clone whose remote has main and stable both at 0.2.0."""
    remote, clone = tmp_path / "remote.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True, capture_output=True)
    (clone / "scripts" / "fork").mkdir(parents=True)
    shutil.copy(SCRIPT, clone / "scripts" / "fork" / "promote-stable.sh")
    _commit(clone, "0.2.0", "base")
    _git(clone, "push", "-q", "origin", "main", "main:stable")
    return clone


def _run(work, tmp_path, monkeypatch, *args):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    calls = tmp_path / "gh_calls.jsonl"
    monkeypatch.setenv("GH_CALLS", str(calls))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    done = subprocess.run(["bash", "scripts/fork/promote-stable.sh", *args], cwd=work, capture_output=True, text=True, check=False)
    recorded = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    return done.returncode, done.stdout + done.stderr, recorded


def test_nothing_to_promote_opens_no_pull_request(work, tmp_path, monkeypatch):
    code, out, calls = _run(work, tmp_path, monkeypatch)
    assert code == 0 and "already contains everything" in out, out
    assert calls == []


def test_a_version_bump_is_called_out_in_the_pull_request(work, tmp_path, monkeypatch):
    _commit(work, "0.3.0", "bump 0.3.0")
    _commit(work, "0.3.0", "a feature")
    _git(work, "push", "-q", "origin", "main")
    code, out, calls = _run(work, tmp_path, monkeypatch)
    assert code == 0, out
    (call,) = calls
    assert call[:2] == ["pr", "create"]
    assert call[call.index("--base") + 1] == "stable" and call[call.index("--head") + 1] == "main"
    title, body = call[call.index("--title") + 1], call[call.index("--body") + 1]
    assert "2 commits" in title and "0.3.0" in title
    assert "0.2.0 -> 0.3.0" in body and "tags v0.3.0" in body
    assert "a feature" in body and "bump 0.3.0" in body


def test_without_a_version_bump_it_says_nothing_will_be_released(work, tmp_path, monkeypatch):
    _commit(work, "0.2.0", "just a fix")
    _git(work, "push", "-q", "origin", "main")
    code, out, calls = _run(work, tmp_path, monkeypatch)
    assert code == 0, out
    body = calls[0][calls[0].index("--body") + 1]
    assert "nothing will be tagged or released" in body


def test_dry_run_prints_and_does_not_call_gh(work, tmp_path, monkeypatch):
    _commit(work, "0.3.0", "bump 0.3.0")
    _git(work, "push", "-q", "origin", "main")
    code, out, calls = _run(work, tmp_path, monkeypatch, "--dry-run")
    assert code == 0 and "Promote main to stable" in out and "bump 0.3.0" in out, out
    assert calls == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `scripts/run_tests.sh tests/scripts/test_promote_stable.py -v`
Expected: FAIL (the script does not exist, so `fixture` copy raises `FileNotFoundError`).

- [ ] **Step 3: Implement**

Create `scripts/fork/promote-stable.sh`:

```bash
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
```

Then: `chmod +x scripts/fork/promote-stable.sh` and `git update-index --chmod=+x scripts/fork/promote-stable.sh` (the other scripts in the folder are executable).

- [ ] **Step 4: Run to verify it passes**

Run: `scripts/run_tests.sh tests/scripts/test_promote_stable.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/fork/promote-stable.sh tests/scripts/test_promote_stable.py
git commit -m "Add promote-stable.sh to open the main to stable promotion PR

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/private-fork/RELEASING.md`
- Modify: `docs/private-fork/REPO_PROTECTION.md`
- Modify: `docs/private-fork/FORK_DECISIONS.md`
- Modify: `deploy/dokploy/README.md`

**Interfaces:**
- Consumes: the behaviour built in Tasks 1-5. No code.

- [ ] **Step 1: RELEASING.md**

Replace the opening paragraph and flow diagram (`A release is a **version bump that gets merged**...` through the closing code fence of the diagram) with:

````markdown
A release is a **version bump that has been promoted to `stable`**. Nothing is tagged by hand and nothing is published by a person.

```
scripts/fork/bump-version.sh next         one command, four files (see "Versions" below)
        |
   pull request  ->  merge to main         the hot line; every merge builds the rolling main-latest pre-release
        |
   scripts/fork/promote-stable.sh          opens a reviewed PR: main -> stable
        |
   merge to stable   Dokploy redeploys every client project that follows stable
        |
   tag-release.yml   sees a version file changed on stable, creates tag v0.3.0 (with RELEASE_TOKEN)
        |
   release-light.yml  the tag push builds HermesLight-0.3.0-win-x64.exe and publishes the GitHub release
```

## Versions

The first stable release is `0.3.0`. Each later release bumps the patch (`0.3.1` ... `0.3.9`); after `X.Y.9` the next release is
`X.(Y+1).0`, so `0.3.9` is followed by `0.4.0`. `scripts/fork/bump-version.sh next` computes it; an explicit
`bump-version.sh 0.3.0` is accepted if it follows the rule. `tag-release.sh` refuses a patch above 9 and refuses a commit that is not on
`origin/stable`. Builds from `main` keep the last released version and are told apart by the commit in their title.
````

Replace the **Cutting a release** code block with:

````markdown
```
scripts/fork/bump-version.sh next      # package.json, pyproject.toml, package-lock.json, uv.lock (runs `uv lock`)
git switch -c release-0.3.1 && git commit -am "chore(release): 0.3.1" && git push -u origin release-0.3.1
gh pr create --base main               # review, merge with a merge commit
scripts/fork/promote-stable.sh         # when main is tested: opens the main -> stable PR; review and merge it
```
````

Change `as `HermesLight-0.2.0-win-x64.msix` plus its `.sha256`. The installer is unsigned (see the release notes for the install step).` to `as `HermesLight-<version>-win-x64.exe` plus its `.sha256`. The installer is unsigned, so Windows SmartScreen warns first: choose **More info**, then **Run anyway**.`

Change the sentence about the token workflow `which runs for pushes to `main` (reviewed code) and manual runs on `main`` to `which runs for pushes to `stable` (reviewed code) and manual runs on `stable``.

Add a section at the end, `## Hotfixes`: "Fix on `main` first, merge, then cherry-pick the fix into a branch from `stable` and open a PR into `stable`. Never fix only on `stable`; the two lines must not diverge."

- [ ] **Step 2: REPO_PROTECTION.md**

In the table after the `main` rows, add rows for ruleset `stable`: `| Ruleset `stable` (`refs/heads/stable`) | Same as `main`: pull request, 1 approval, merge commits only, the four `Fork CI` checks, no deletion, no force push | Client deployments and releases follow this branch; it moves only by promotion PR. |`.

Change the bullet `- **A push to `main` is a production deploy.** ...` to say that client Dokploy projects follow `stable`, so `stable` is the production line, and `main` is production only for the maintainer's own development company.

Change `- **Dokploy:** auto-deploy follows `main`;` to `- **Dokploy:** client projects auto-deploy `stable`;`.

- [ ] **Step 3: FORK_DECISIONS.md**

Replace `**L5. A release is a merged version bump**` text's first sentence so it reads `**L5. A release is a version bump promoted to `stable`** (decided 2026-10-23; amended 2026-10-30 by L6).` Keep the rest, changing "a PR, a merge; then `tag-release.yml` tags" to "a PR, a merge to `main`, a promotion to `stable`; then `tag-release.yml` tags".

Add after L5:

```markdown
- **L6. `stable` is the line clients and testers follow** (decided 2026-10-30). A long-lived `stable` branch, promoted from `main` by
  reviewed PR (`scripts/fork/promote-stable.sh`), protected like `main` (`.github/rulesets/stable.json`). Every client Dokploy
  project deploys from it, so merging to `stable` updates them all; release tags are cut from it. `main` stays the hot line and
  keeps the rolling `main-latest` pre-release. Versions: first stable release `0.3.0`, patch `0.3.1` to `0.3.9`, then `0.4.0`.
  Client self-update is deferred: testers reinstall the unsigned NSIS `.exe`. Design and plan:
  `2026-10-30-stable-branch-design.md`, `2026-10-30-stable-branch-plan.md`.
```

- [ ] **Step 4: deploy/dokploy/README.md**

In "Create a company" step 1, change `branch: the one you deploy from,` to `branch: `stable` (the line every client follows; a development company of your own may use `main`),`.

Change the **Updates** bullet to: `- **Updates:** merge a promotion PR into `stable`; Dokploy rebuilds the image on the server for every project that follows it. ...` (keep the sentence about the first build being slow).

- [ ] **Step 5: Verify no stale statements remain**

Run: `grep -rn "\.msix\|merge to main\|pushes to .main" docs/private-fork/RELEASING.md docs/private-fork/REPO_PROTECTION.md deploy/dokploy/README.md`
Expected: no line that still describes the old flow (a mention of `main-latest` or "merge to main" in the new diagram is correct).

- [ ] **Step 6: Commit**

```bash
git add docs/private-fork deploy/dokploy/README.md
git commit -m "Document the stable branch, promotion flow and 0.3.x versions

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Open the PR and verify in CI

**Files:** none.

- [ ] **Step 1: Push and open the PR**

```bash
git push -q origin stable-branch-design
gh pr create --base main --head stable-branch-design --title "Add a stable branch, promotion flow and 0.3.x versioning" \
  --body "Implements docs/private-fork/2026-10-30-stable-branch-design.md following docs/private-fork/2026-10-30-stable-branch-plan.md. Scripts and tests, the stable ruleset, the Tag release trigger moved to stable, and docs. Nothing takes effect until the maintainer rollout in the plan is done."
```

- [ ] **Step 2: Watch the checks**

Run: `gh pr checks <number> --watch`
Expected: all four required checks pass. The posix script tests ran in `Access, invites and deployment (Python)`; confirm in its log that `test_promote_stable.py`, `test_check_github_protection.py` and `test_release_version_scripts.py` ran (not skipped).

---

### Task 8: Maintainer rollout (repository owner; not automatable)

Do these **in order, after the PR in Task 7 is approved and merged**. Each needs admin rights on `sentio-one/sentio`.

- [ ] **Step 1: Bump to 0.3.0 on main.** `scripts/fork/bump-version.sh 0.3.0`, branch, PR into `main`, merge. (`Tag release` no longer fires on `main`, so nothing is tagged yet.)
- [ ] **Step 2: Create `stable` from main.** `git fetch origin && git push origin origin/main:refs/heads/stable`.
- [ ] **Step 3: Apply the ruleset.** As the owner (`gh auth login` as `sentio-one`): `scripts/fork/apply-github-protection.sh` (dry run), then `scripts/fork/apply-github-protection.sh --apply`. Verify: `scripts/fork/check-github-protection.sh` ends with `github protection: ok` and lists `stable: pull_request`.
- [ ] **Step 4: Cut the first release.** Branch creation does not reliably trigger the path-filtered workflow, so run it by hand: Actions, **Tag release**, **Run workflow**, branch **stable**. Expected: it creates `v0.3.0`, then `Release Light Desktop` builds and publishes `HermesLight-0.3.0-win-x64.exe`, marked **Latest**. If it fails with `RELEASE_TOKEN is not set` or a push error, rotate the token per `RELEASING.md`.
- [ ] **Step 5: Repoint Dokploy.** For every client compose project: Source branch `stable`, auto-deploy on. Redeploy once and check the gateway health.
- [ ] **Step 6: Try a promotion.** After the next merge to `main`, run `scripts/fork/promote-stable.sh --dry-run`, then without `--dry-run`; review and merge the PR; confirm the clients redeployed.

---

## Self-review

- **Spec coverage:** ruleset and drift check (Task 4); promote script (Task 5); `tag-release.yml` to stable (Task 3); `tag-release.sh` stable guard (Task 2) and version rule (Task 1); `bump-version.sh` next (Task 1); docs including L5 amendment (Task 6); maintainer-only steps and start point (Task 8); `release-light.yml` deliberately unchanged. Client self-update stays out of scope as the spec says.
- **Placeholders:** none; every code step has the code.
- **Consistency:** `TAG_STABLE_BRANCH`, `PROMOTE_REMOTE`, the `stable` ruleset name, the error strings `patch above 9`, `is not on origin/stable`, `single digit`, and the test assertions on them match across tasks.
- **Known limits:** the bash tests cannot run on Windows; Task 7 confirms them in CI. The checker refactor in Task 4 keeps the substrings the existing tests assert.
