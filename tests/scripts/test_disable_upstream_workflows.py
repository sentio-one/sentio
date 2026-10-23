"""scripts/fork/disable-upstream-workflows.sh with real git remotes and a fake `gh`.

A workflow is upstream's when its file exists in upstream/main. The fork's own workflows must never be disabled, including ones added
after the script was written: an earlier version kept two workflows by name and would have switched off every newer fork workflow.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "fork" / "disable-upstream-workflows.sh"

pytestmark = [pytest.mark.platforms("posix"), pytest.mark.skipif(not shutil.which("jq"), reason="gh's --jq is faked with jq")]

FAKE_GH = '''#!{python}
import json, os, subprocess, sys
state = json.load(open(os.environ["FAKE_GH_STATE"]))
args = sys.argv[1:]
if args[:2] == ["workflow", "disable"]:
    open(os.environ["FAKE_GH_LOG"], "a").write(args[2] + " " + " ".join(args[3:]) + "\\n")
    sys.exit(0)
assert args[0] == "api" and args[1] == "repos/" + state["repo"] + "/actions/workflows", args
jq = args[args.index("--jq") + 1]
print(subprocess.run(["jq", "-r", jq], input=json.dumps({{"workflows": state["workflows"]}}), text=True, capture_output=True,
                     check=True).stdout, end="")
'''

UPSTREAM_FILES = {"ci.yaml": "CI", "lint.yml": "Lint", "catalog-tests.yml": "Catalog tests"}
FORK_FILES = {"fork-ci.yml": "Fork CI", "tag-release.yml": "Tag release", "protection-drift.yml": "Protection drift"}


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A clone whose `origin` is the fork and whose `upstream` carries the upstream workflow files."""
    upstream_work, upstream, clone = tmp_path / "up-work", tmp_path / "upstream.git", tmp_path / "clone"
    (upstream_work / ".github" / "workflows").mkdir(parents=True)
    for name in UPSTREAM_FILES:
        (upstream_work / ".github" / "workflows" / name).write_text("name: x\n", encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main", str(upstream_work))
    _git(upstream_work, "add", "-A")
    _git(upstream_work, "commit", "-qm", "upstream")
    _git(tmp_path, "clone", "-q", "--bare", str(upstream_work), str(upstream))

    (clone / "scripts" / "fork").mkdir(parents=True)
    shutil.copy(SCRIPT, clone / "scripts" / "fork" / SCRIPT.name)
    _git(tmp_path, "init", "-q", "-b", "main", str(clone))
    _git(clone, "remote", "add", "origin", "https://github.com/the-fork/app.git")
    _git(clone, "remote", "add", "upstream", str(upstream))

    workflows = [{"id": i, "state": "active", "path": f".github/workflows/{file}", "name": name}
                 for i, (file, name) in enumerate({**UPSTREAM_FILES, **FORK_FILES}.items(), start=1)]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    state, log = tmp_path / "state.json", tmp_path / "gh.log"
    state.write_text(json.dumps({"repo": "the-fork/app", "workflows": workflows}), encoding="utf-8")
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}:" + os.environ["PATH"])
    monkeypatch.delenv("HERMES_FORK_REPO", raising=False)
    return clone, log, state, workflows


def _run(clone, *args):
    done = subprocess.run(["bash", "scripts/fork/disable-upstream-workflows.sh", *args], cwd=clone, capture_output=True, text=True,
                          check=False)
    return done.returncode, done.stdout + done.stderr


def test_a_dry_run_names_only_the_workflows_that_came_from_upstream(setup):
    clone, log, _, _ = setup
    code, out = _run(clone)
    assert code == 0, out
    for name in UPSTREAM_FILES.values():
        assert f"would disable: {name} " in out
    for name in FORK_FILES.values():
        assert name not in out
    assert "repository: the-fork/app" in out and not log.exists()


def test_apply_disables_exactly_the_upstream_workflows_on_the_fork_repository(setup):
    clone, log, _, workflows = setup
    code, out = _run(clone, "--apply")
    assert code == 0, out
    ids_by_name = {w["name"]: w["id"] for w in workflows}
    calls = log.read_text(encoding="utf-8").splitlines()
    assert sorted(calls) == sorted(f"{ids_by_name[name]} -R the-fork/app" for name in UPSTREAM_FILES.values())


def test_an_already_disabled_workflow_is_skipped_and_a_new_fork_workflow_is_never_touched(setup):
    clone, log, state, workflows = setup
    for workflow in workflows:
        if workflow["name"] == "CI":
            workflow["state"] = "disabled_manually"
    workflows.append({"id": 99, "state": "active", "path": ".github/workflows/brand-new-fork-workflow.yml", "name": "Brand new"})
    state.write_text(json.dumps({"repo": "the-fork/app", "workflows": workflows}), encoding="utf-8")
    code, out = _run(clone, "--apply")
    assert code == 0, out
    disabled = {line.split()[0] for line in log.read_text(encoding="utf-8").splitlines()}
    ids = {w["name"]: str(w["id"]) for w in workflows}
    assert disabled == {ids["Lint"], ids["Catalog tests"]}
    assert ids["Brand new"] not in disabled


def test_without_an_upstream_remote_it_stops_instead_of_guessing(setup):
    clone, log, _, _ = setup
    subprocess.run(["git", "remote", "remove", "upstream"], cwd=clone, check=True, capture_output=True)
    code, out = _run(clone, "--apply")
    assert code == 1 and "no upstream/main" in out and not log.exists()
