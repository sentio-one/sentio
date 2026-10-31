"""scripts/fork/refresh-main-release.sh against a fake `gh` that records every call.

The rolling `main-latest` pre-release is what tells people a new main build exists, so what matters is the order (the old release is
removed, then the new one created at the new commit), that it can never become the repository's "Latest" release, and that a wrong
or ambiguous set of files is never published.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "fork" / "refresh-main-release.sh"
SHA = "0123456789abcdef0123456789abcdef01234567"
HASH = "A4C86467E0A84BF03F9F2C0F05E638FD46048124593045150AA33683A07E4884"

pytestmark = [pytest.mark.platforms("posix"), pytest.mark.skipif(not shutil.which("jq"), reason="gh's --jq is faked with jq")]

FAKE_GH = '''#!{python}
import json, os, subprocess, sys
args = sys.argv[1:]
open(os.environ["FAKE_GH_LOG"], "a").write(json.dumps(args) + "\\n")
if args[:2] == ["release", "delete"]:
    sys.exit(0 if os.environ.get("FAKE_GH_HAS_RELEASE") == "1" else 1)
if args[:2] == ["release", "create"]:
    sys.exit(1 if os.environ.get("FAKE_GH_CREATE_FAILS") == "1" else 0)
if args[0] == "api":
    body = {{"commit": {{"message": "Merge pull request #9 from x/y\\n\\nthe body"}}}}
    print(subprocess.run(["jq", "-r", args[args.index("--jq") + 1]], input=json.dumps(body), text=True, capture_output=True,
                         check=True).stdout, end="")
    sys.exit(0)
sys.exit(2)
'''


@pytest.fixture
def run(tmp_path, monkeypatch):
    bin_dir, release = tmp_path / "bin", tmp_path / "release"
    bin_dir.mkdir()
    release.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    (release / "SentioOne-0.1.0-win-x64.exe").write_bytes(b"exe")
    (release / "SentioOne-0.1.0-win-x64.exe.sha256").write_text(f"{HASH}  SentioOne-0.1.0-win-x64.exe\n", encoding="utf-8")
    log = tmp_path / "gh.log"
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("GH_REPO", "the-fork/app")
    monkeypatch.setenv("RELEASE_DIR", str(release))

    def go():
        done = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, check=False)
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        return done.returncode, done.stdout + done.stderr, calls
    return go, release


def _verb(call):
    return " ".join(call[:2])


def test_the_old_release_is_removed_then_a_new_prerelease_is_created_at_the_new_commit(run, monkeypatch):
    go, release = run
    monkeypatch.setenv("FAKE_GH_HAS_RELEASE", "1")
    code, out, calls = go()
    assert code == 0 and "main-latest now points at 0123456" in out, out
    assert [_verb(c) for c in calls if c[0] == "release"] == ["release delete", "release create"]
    delete = next(c for c in calls if _verb(c) == "release delete")
    assert delete[2] == "main-latest" and "--cleanup-tag" in delete  # the tag moves with the release
    create = next(c for c in calls if _verb(c) == "release create")
    assert create[2] == "main-latest"
    assert str(release / "SentioOne-0.1.0-win-x64.exe") in create and str(release / "SentioOne-0.1.0-win-x64.exe.sha256") in create
    assert create[create.index("--target") + 1] == SHA
    assert "--prerelease" in create and "--latest=false" in create  # never replaces the real Latest release
    assert "0123456" in create[create.index("--title") + 1]
    notes = create[create.index("--notes") + 1]
    assert "Merge pull request #9 from x/y" in notes and "the body" not in notes  # first line of the commit message only
    assert HASH in notes and "not a stable release" in notes
    assert "SmartScreen" in notes and "Run anyway" in notes  # the unsigned-installer step is in the notes


def test_the_first_ever_run_has_nothing_to_delete_and_still_publishes(run):
    go, _ = run
    code, out, calls = go()  # fake `gh release delete` exits 1: no such release
    assert code == 0, out
    assert [_verb(c) for c in calls if c[0] == "release"] == ["release delete", "release create"]


def test_a_failed_create_fails_the_job_instead_of_leaving_a_silent_gap(run, monkeypatch):
    go, _ = run
    monkeypatch.setenv("FAKE_GH_CREATE_FAILS", "1")
    code, _, _ = go()
    assert code != 0


@pytest.mark.parametrize("break_it", ["no_installer", "two_installers", "no_checksum"])
def test_it_refuses_to_publish_an_unexpected_file_set_and_touches_no_release(run, break_it):
    go, release = run
    if break_it == "no_installer":
        (release / "SentioOne-0.1.0-win-x64.exe").unlink()
    elif break_it == "two_installers":
        shutil.copy(release / "SentioOne-0.1.0-win-x64.exe", release / "SentioOne-0.2.0-win-x64.exe")
    else:
        (release / "SentioOne-0.1.0-win-x64.exe.sha256").unlink()
    code, out, calls = go()
    assert code == 1 and "expected one installer and one checksum" in out, out
    assert not any(c[0] == "release" for c in calls)
