"""scripts/fork/check-github-protection.sh against a fake `gh` that serves canned API responses.

The passing path cannot be seen live (applying protection needs admin rights the author lacks), so the rulesets committed in
.github/rulesets are the "correctly protected" state here. Each failing case is one way the real repository was or could be wrong,
including the original one: rulesets naming a branch that does not exist.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "fork" / "check-github-protection.sh"
RULESET_FILES = sorted((REPO / ".github" / "rulesets").glob("*.json"))
NAME = "test-owner/test-repo"

pytestmark = [
    pytest.mark.platforms("posix"),
    pytest.mark.skipif(not shutil.which("jq"), reason="the checker needs jq"),
]

FAKE_GH = '''#!{python}
import json, os, subprocess, sys
state = json.load(open(os.environ["FAKE_GH_STATE"]))
args = sys.argv[1:]
assert args[0] == "api", args
url = args[1]
jq = args[args.index("--jq") + 1] if "--jq" in args else None
prefix = "repos/" + state["name"]
if url == prefix + "/rulesets":
    body = [{{"id": r["id"], "name": r["name"]}} for r in state["rulesets"]]
elif url.startswith(prefix + "/rulesets/"):
    body = next(r for r in state["rulesets"] if str(r["id"]) == url.rsplit("/", 1)[1])
elif url.startswith(prefix + "/branches/"):
    branch = url.split("/branches/", 1)[1]
    if branch not in state["branches"]:
        sys.exit(1)
    body = {{"name": branch}}
elif url == prefix:
    body = state["repo"]
else:
    sys.exit(1)
if jq:
    print(subprocess.run(["jq", "-r", jq], input=json.dumps(body), text=True, capture_output=True, check=True).stdout, end="")
else:
    print(json.dumps(body))
'''


def _protected_state():
    rulesets = []
    for index, path in enumerate(RULESET_FILES, start=1):
        ruleset = json.loads(path.read_text(encoding="utf-8"))
        rulesets.append({"id": index, "source_type": "Repository", **ruleset})
    return {"name": NAME, "rulesets": rulesets, "branches": ["main", "stable", "upstream-merge"],
            "repo": {"default_branch": "main", "allow_squash_merge": False, "allow_rebase_merge": False}}


def _run(tmp_path, monkeypatch, state):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
    gh.chmod(0o755)
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv("FAKE_GH_STATE", str(state_file))
    monkeypatch.setenv("HERMES_FORK_REPO", NAME)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    done = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, cwd=REPO, timeout=60, check=False)
    return done.returncode, done.stdout + done.stderr


def _ruleset(state, name):
    return next(r for r in state["rulesets"] if r["name"] == name)


def test_the_committed_rulesets_satisfy_the_checker(tmp_path, monkeypatch):
    code, out = _run(tmp_path, monkeypatch, _protected_state())
    assert code == 0 and "github protection: ok" in out, out
    assert "FAIL" not in out


def test_the_checker_works_with_a_jq_that_prints_crlf(tmp_path, monkeypatch):
    """jq built for Windows ends every output line with CRLF. The checker compared those lines with plain strings,
    so a correctly protected repository reported required checks missing and a branch that exists as missing."""
    real_jq = shutil.which("jq")
    shim_dir = tmp_path / "crlf-jq"
    shim_dir.mkdir()
    shim = shim_dir / "jq"
    shim.write_text(f'#!/bin/bash\nset -o pipefail\n"{real_jq}" "$@" | sed \'s/$/\\r/\'\n', encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")
    code, out = _run(tmp_path, monkeypatch, _protected_state())
    assert code == 0 and "github protection: ok" in out, out
    assert "FAIL" not in out


def test_a_ruleset_naming_a_branch_that_does_not_exist_protects_nothing(tmp_path, monkeypatch):
    """The original mistake: active rulesets whose ref pattern was `refs/heads/protect-main`, not `main`."""
    state = _protected_state()
    state["rulesets"] = [copy.deepcopy(_ruleset(state, "main"))]
    state["rulesets"][0]["conditions"]["ref_name"]["include"] = ["refs/heads/protect-main"]
    code, out = _run(tmp_path, monkeypatch, state)
    assert code == 1
    assert "no active ruleset covers main" in out
    assert "refs/heads/protect-main, which does not exist" in out


def _allow_squash(state):
    rule = next(r for r in _ruleset(state, "main")["rules"] if r["type"] == "pull_request")
    rule["parameters"]["allowed_merge_methods"] = ["merge", "squash", "rebase"]


def _drop_sandbox_check(state):
    rule = next(r for r in _ruleset(state, "main")["rules"] if r["type"] == "required_status_checks")
    checks = rule["parameters"]["required_status_checks"]
    rule["parameters"]["required_status_checks"] = [c for c in checks if "Sandbox" not in c["context"]]


def _disable_stable(state):
    _ruleset(state, "stable")["enforcement"] = "disabled"


def _allow_squash_on_stable(state):
    rule = next(r for r in _ruleset(state, "stable")["rules"] if r["type"] == "pull_request")
    rule["parameters"]["allowed_merge_methods"] = ["merge", "squash"]


def _drop_ruleset(name):
    return lambda state: state.__setitem__("rulesets", [r for r in state["rulesets"] if r["name"] != name])


def _disable_main(state):
    _ruleset(state, "main")["enforcement"] = "disabled"


def _squash_on(state):
    state["repo"]["allow_squash_merge"] = True


@pytest.mark.parametrize("mutate,expected", [
    (_allow_squash, 'allowed merge methods are ["merge","squash","rebase"]'),
    (_drop_sandbox_check, "required check missing: Sandbox cannot reach the Docker daemon"),
    (_drop_ruleset("release-tags"), "v* tags are not protected"),
    (_drop_ruleset("upstream-merge"), "upstream-merge branch is not protected"),
    (_disable_main, "no active ruleset covers main"),
    (_squash_on, "repository setting allow_squash_merge is on"),
    (_disable_stable, "no active ruleset covers stable"),
    (_allow_squash_on_stable, "stable: allowed merge methods are"),
], ids=["squash-allowed", "missing-check", "no-tag-rule", "no-upstream-merge-rule", "main-disabled", "squash-setting",
        "stable-disabled", "stable-squash-allowed"])
def test_each_gap_is_reported_and_fails_the_check(tmp_path, monkeypatch, mutate, expected):
    state = _protected_state()
    mutate(state)
    code, out = _run(tmp_path, monkeypatch, state)
    assert code == 1 and expected in out, out


def test_the_fork_is_origin_even_when_an_upstream_remote_exists(tmp_path, monkeypatch):
    """`gh repo view` resolves to a clone's `upstream` remote (NousResearch/hermes-agent here), so the checker once audited
    upstream's protection and reported it as ours. The script must name the repository from `origin`."""
    clone = tmp_path / "clone"
    (clone / "scripts" / "fork").mkdir(parents=True)
    shutil.copy(SCRIPT, clone / "scripts" / "fork" / SCRIPT.name)
    shutil.copytree(REPO / ".github" / "rulesets", clone / ".github" / "rulesets")
    for args in (["init", "-q"], ["remote", "add", "origin", "https://github.com/the-fork/app.git"],
                 ["remote", "add", "upstream", "git@github.com:the-upstream/app.git"]):
        subprocess.run(["git", *args], cwd=clone, check=True)
    state = _protected_state()
    state["name"] = "the-fork/app"  # the fake answers only for this repository; asking about any other fails
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv("FAKE_GH_STATE", str(tmp_path / "state.json"))
    monkeypatch.delenv("HERMES_FORK_REPO", raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    done = subprocess.run(["bash", str(clone / "scripts" / "fork" / SCRIPT.name)], capture_output=True, text=True,
                          cwd=clone, timeout=60, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "repository: the-fork/app" in done.stdout and "the-upstream" not in done.stdout
