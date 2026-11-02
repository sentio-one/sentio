"""Native Windows junctions must obey granted workspace boundaries."""

import base64
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from hermes_cli.web_routers.files import router


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    profile = home / "profiles" / "alpha"
    workspace = profile / "workspace"
    workspace.mkdir(parents=True)
    (profile / "config.yaml").write_text("profile: alpha\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return SimpleNamespace(home=home, profile=profile, workspace=workspace)


@pytest.fixture
def granted_client():
    app = FastAPI()

    @app.middleware("http")
    async def stamp_profile_grant(request: Request, call_next):
        request.state.access_allowed_profiles = frozenset({"alpha"})
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def junction_factory(tmp_path):
    junctions = []

    def create(link: Path, target: Path) -> Path:
        assert link.parent.is_dir()
        assert target.is_dir()
        assert link.parent.resolve().is_relative_to(tmp_path.resolve())
        assert target.resolve().is_relative_to(tmp_path.resolve())
        result = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        junctions.append(link)
        return link

    yield create

    # os.rmdir removes this junction entry without traversing or deleting its target.
    for junction in reversed(junctions):
        if junction.exists():
            os.rmdir(junction)


def _files_read(client, path):
    return client.get("/api/files/read", params={"profile": "alpha", "path": path})


def _files_upload(client, path, content=b"updated"):
    data_url = "data:text/plain;base64," + base64.b64encode(content).decode("ascii")
    return client.post(
        "/api/files/upload?profile=alpha",
        json={"path": path, "data_url": data_url, "overwrite": True},
    )


def _fs_read(client, path):
    return client.get(
        "/api/fs/read-text", params={"profile": "alpha", "path": str(path)}
    )


def _fs_write(client, path, content="changed"):
    return client.post(
        "/api/fs/write-text?profile=alpha",
        json={"path": str(path), "content": content},
    )


@pytest.mark.platforms("windows")
def test_granted_files_allow_safe_internal_junction(
    profile_env, granted_client, junction_factory,
):
    workspace = profile_env.workspace
    target_dir = workspace / "real"
    target_dir.mkdir()
    target = target_dir / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    alias = junction_factory(workspace / "alias", target_dir)

    managed_read = _files_read(granted_client, "alias/notes.txt")
    fs_read = _fs_read(granted_client, alias / "notes.txt")
    managed_write = _files_upload(granted_client, "alias/notes.txt")
    fs_write = _fs_write(granted_client, alias / "notes.txt")

    assert managed_read.status_code == 200
    assert managed_read.json()["data_url"].endswith(base64.b64encode(b"hello").decode())
    assert fs_read.status_code == 200 and fs_read.json()["text"] == "hello"
    assert managed_write.status_code == 200
    assert fs_write.status_code == 200
    assert target.read_text(encoding="utf-8") == "changed"
    assert target_dir.is_dir()


@pytest.mark.platforms("windows")
def test_granted_files_block_junction_escape_for_reads_and_writes(
    profile_env, granted_client, junction_factory,
):
    workspace = profile_env.workspace
    outside = profile_env.profile / "outside-workspace"
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("private", encoding="utf-8")
    escape = junction_factory(workspace / "escape", outside)

    managed_read = _files_read(granted_client, "escape/secret.txt")
    managed_write = _files_upload(granted_client, "escape/new.txt", b"must not write")
    fs_read = _fs_read(granted_client, escape / "secret.txt")
    fs_write = _fs_write(granted_client, escape / "secret.txt", "must not write")

    assert managed_read.status_code == 403
    assert managed_write.status_code == 403
    assert fs_read.status_code == 403
    assert fs_write.status_code == 403
    assert target.read_text(encoding="utf-8") == "private"
    assert not (outside / "new.txt").exists()
