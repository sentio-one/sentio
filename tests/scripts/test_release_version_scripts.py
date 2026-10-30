"""scripts/fork/tag-release.sh and bump-version.sh against real git repositories (a bare remote plus a working clone).

The tagger decides, on every merge that touches a version file, whether to create the release tag that starts the installer build,
so each way it must refuse (or do nothing) is a case here.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "fork"

pytestmark = pytest.mark.platforms("posix")

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def _git(cwd, *args):
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _write_versions(work, desktop, python=None):
    (work / "apps" / "desktop").mkdir(parents=True, exist_ok=True)
    (work / "apps" / "desktop" / "package.json").write_text(json.dumps({"name": "hermes", "version": desktop}), encoding="utf-8")
    (work / "pyproject.toml").write_text(f'[project]\nname = "hermes-agent"\nversion = "{python or desktop}"\n', encoding="utf-8")


@pytest.fixture
def clone(tmp_path):
    """A working clone of a bare remote that already carries tag v0.1.0 at version 0.1.0."""
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True, capture_output=True)
    (work / "scripts" / "fork").mkdir(parents=True)
    for name in ("tag-release.sh", "bump-version.sh"):
        shutil.copy(SCRIPTS / name, work / "scripts" / "fork" / name)
    _write_versions(work, "0.1.0")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "base")
    _git(work, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    _git(work, "push", "-q", "origin", "main", "main:stable", "v0.1.0")
    return work


def _tagger(work, *args):
    done = subprocess.run(["bash", "scripts/fork/tag-release.sh", *args], cwd=work, capture_output=True, text=True, check=False)
    return done.returncode, done.stdout + done.stderr


def _remote_tags(work):
    return sorted(line.split("refs/tags/")[1] for line in _git(work, "ls-remote", "--tags", "--refs", "origin").splitlines())


def _bump_commit(work, desktop, python=None):
    _write_versions(work, desktop, python)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", f"bump {desktop}")
    _git(work, "push", "-q", "origin", "main", "main:stable")


def test_a_new_version_gets_an_annotated_tag_on_the_commit_that_bumped_it(clone):
    _bump_commit(clone, "0.2.0")
    code, out = _tagger(clone, "--push")
    assert code == 0 and "pushed v0.2.0" in out, out
    assert _remote_tags(clone) == ["v0.1.0", "v0.2.0"]
    assert _git(clone, "rev-parse", "v0.2.0^{commit}") == _git(clone, "rev-parse", "HEAD")
    assert _git(clone, "cat-file", "-t", "v0.2.0") == "tag"  # annotated, not lightweight


def test_a_version_that_already_has_a_tag_is_left_alone(clone):
    code, out = _tagger(clone, "--push")
    assert code == 0 and "already exists" in out, out
    assert _remote_tags(clone) == ["v0.1.0"]


def test_without_push_it_only_says_what_it_would_do(clone):
    _bump_commit(clone, "0.2.0")
    code, out = _tagger(clone)
    assert code == 0 and "would tag" in out and "v0.2.0" in out, out
    assert _remote_tags(clone) == ["v0.1.0"]


@pytest.mark.parametrize("desktop,python,expected", [
    ("0.2.0", "0.1.0", "bump both"),          # a half-done bump
    ("0.2", "0.2", "not plain MAJOR.MINOR.PATCH"),
    ("0.2.0-rc1", "0.2.0-rc1", "not plain MAJOR.MINOR.PATCH"),
    ("0.0.9", "0.0.9", "not newer than the latest tag v0.1.0"),  # going backwards
])
def test_it_refuses_a_version_it_should_not_release(clone, desktop, python, expected):
    _bump_commit(clone, desktop, python)
    code, out = _tagger(clone, "--push")
    assert code == 1 and expected in out, out
    assert _remote_tags(clone) == ["v0.1.0"]


def test_a_prerelease_tag_does_not_block_the_next_release(clone):
    _git(clone, "tag", "-a", "v0.3.0-rc1", "-m", "rc")
    _git(clone, "push", "-q", "origin", "v0.3.0-rc1")
    _bump_commit(clone, "0.2.0")
    code, out = _tagger(clone, "--push")
    assert code == 0, out
    assert "v0.2.0" in _remote_tags(clone)


def test_bump_version_updates_every_place_and_refuses_going_backwards(clone):
    (clone / "package-lock.json").write_text(
        '{\n  "packages": {\n    "apps/desktop": {\n      "name": "hermes",\n      "version": "0.0.0",\n      "dependencies": {}\n    }\n  }\n}\n',
        encoding="utf-8")
    path = {"PATH": "/usr/bin:/bin"}  # no uv: the script must say so instead of failing
    done = subprocess.run(["bash", "scripts/fork/bump-version.sh", "0.2.0"], cwd=clone, capture_output=True, text=True, check=False,
                          env={**path})
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads((clone / "apps/desktop/package.json").read_text())["version"] == "0.2.0"
    assert re.search(r'^version = "0.2.0"$', (clone / "pyproject.toml").read_text(), re.MULTILINE)
    assert json.loads((clone / "package-lock.json").read_text())["packages"]["apps/desktop"]["version"] == "0.2.0"
    assert "uv.lock" in done.stderr  # told to lock, since uv was not on PATH
    for refused in ("0.2.0", "0.1.5", "banana"):
        again = subprocess.run(["bash", "scripts/fork/bump-version.sh", refused], cwd=clone, capture_output=True, text=True,
                               check=False, env={**path})
        assert again.returncode != 0, refused


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


def test_it_refuses_a_commit_that_has_not_been_promoted_to_stable(clone):
    _write_versions(clone, "0.2.0")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "bump on main only")
    _git(clone, "push", "-q", "origin", "main")  # main moves, stable does not
    code, out = _tagger(clone, "--push")
    assert code == 1 and "is not on origin/stable" in out, out
    assert _remote_tags(clone) == ["v0.1.0"]
