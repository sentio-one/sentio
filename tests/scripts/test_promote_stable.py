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
FAKE_GH = '#!{python}\nimport json, os, sys\nopen(os.environ["GH_CALLS"], "a").write(json.dumps(sys.argv[1:]) + chr(10))\n'


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
