"""Granted files use the explicitly selected profile's workspace boundary."""
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from hermes_cli.web_routers.files import router
from hermes_cli.web_server_files import _managed_files_policy, restricted_files_root


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / '.hermes'
    profile = home / 'profiles' / 'alpha'
    ws = profile / 'workspace'
    ws.mkdir(parents=True)
    (profile / 'config.yaml').write_text('secret: 1')
    (ws / 'notes.txt').write_text('hello')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    return SimpleNamespace(home=home, profile=profile, ws=ws)


def request(grants=frozenset({'alpha'}), profile='alpha'):
    return SimpleNamespace(state=SimpleNamespace(access_allowed_profiles=grants), query_params={'profile': profile})


def client(grants=frozenset({'alpha'})):
    app = FastAPI()

    @app.middleware('http')
    async def stamp(req: Request, call_next):
        req.state.access_allowed_profiles = grants
        return await call_next(req)

    app.include_router(router)
    return TestClient(app)


def upload(c, path):
    return c.post('/api/files/upload?profile=alpha', json={
        'path': path, 'data_url': 'data:text/plain;base64,' + base64.b64encode(b'new').decode(),
        'overwrite': True})


def test_root_and_file_operations(env):
    assert restricted_files_root(request()) == env.ws.resolve()
    assert restricted_files_root(request(None)) is None
    c = client()
    listing = c.get('/api/files?profile=alpha')
    assert listing.status_code == 200
    assert listing.json()['locked_root'] == str(env.ws.resolve())
    assert [e['name'] for e in listing.json()['entries']] == ['notes.txt']
    assert c.get('/api/files/read?profile=alpha&path=notes.txt').status_code == 200
    assert upload(c, 'new.txt').status_code == 200
    assert (env.ws / 'new.txt').read_text() == 'new'
    assert c.post('/api/files/mkdir?profile=alpha', json={'path': 'docs'}).status_code == 200
    assert c.request('DELETE', '/api/files?profile=alpha', json={'path': 'new.txt'}).status_code == 200
    assert not (env.ws / 'new.txt').exists()


@pytest.mark.parametrize('grants,profile', [(frozenset(), 'alpha'), (frozenset({'alpha'}), ''),
                                           (frozenset({'alpha'}), 'beta'), (frozenset({'alpha'}), 'current')])
def test_root_refuses_empty_or_ungranted_selector(env, grants, profile):
    with pytest.raises(HTTPException) as exc:
        restricted_files_root(request(grants, profile))
    assert exc.value.status_code == 403


def test_read_guard_does_not_create_workspace(env):
    (env.ws / 'notes.txt').unlink()
    env.ws.rmdir()
    assert _managed_files_policy(request(), create_root=False).locked_root == env.ws.resolve()
    assert not env.ws.exists()


@pytest.mark.parametrize('path', ['../config.yaml', '../../other/workspace/theirs.txt'])
def test_traversal_refused(env, path):
    assert client().get('/api/files/read', params={'profile': 'alpha', 'path': path}).status_code == 400


@pytest.mark.platforms('posix')
def test_symlink_leaf_and_missing_parent_write_escapes_refused(env, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = outside / 'target.txt'
    target.write_text('untouched')
    (env.ws / 'leaf').symlink_to(target)
    (env.ws / 'parent').symlink_to(outside, target_is_directory=True)
    c = client()
    assert c.get('/api/files/read?profile=alpha&path=leaf').status_code == 403
    assert upload(c, 'leaf').status_code == 403
    assert upload(c, 'parent/missing.txt').status_code == 403
    assert target.read_text() == 'untouched'
    assert not (outside / 'missing.txt').exists()


@pytest.mark.platforms('posix')
def test_escaped_workspace_anchor_is_refused_before_creation(env, tmp_path):
    import shutil
    outside = tmp_path / 'outside'
    outside.mkdir()
    source = env.ws
    shutil.rmtree(source)
    source.symlink_to(outside, target_is_directory=True)
    with pytest.raises(HTTPException) as exc:
        restricted_files_root(request())
    assert exc.value.status_code == 403
    assert list(outside.iterdir()) == []


@pytest.mark.platforms('posix')
def test_safe_workspace_symlinks_still_work(env):
    (env.ws / 'alias').symlink_to(env.ws / 'notes.txt')
    c = client()
    assert c.get('/api/files/read?profile=alpha&path=alias').status_code == 200
    assert upload(c, 'alias').status_code == 200
    assert (env.ws / 'notes.txt').read_text() == 'new'

@pytest.mark.platforms('posix')
def test_dangling_leaf_write_escape_is_refused(env, tmp_path):
    missing = tmp_path / 'must-not-be-created.txt'
    (env.ws / 'dangling').symlink_to(missing)
    assert upload(client(), 'dangling').status_code == 403
    assert not missing.exists()


def test_missing_profile_is_not_recreated_from_a_stale_grant(env):
    missing = env.home / 'profiles' / 'gone'
    response = client(frozenset({'gone'})).get('/api/files?profile=gone')
    assert response.status_code in (403, 404)
    assert not missing.exists()


FS_READ_ROUTES = ('list', 'read-text', 'read-data-url', 'download', 'git-root')


def fs(c, route, path=None):
    params = {'profile': 'alpha'}
    if path is not None:
        params['path'] = str(path)
    return c.get('/api/fs/' + route, params=params)


def fs_write(c, path):
    return c.post('/api/fs/write-text?profile=alpha', json={'path': str(path), 'content': 'changed'})


@pytest.mark.parametrize('route', FS_READ_ROUTES)
def test_fs_outside_workspace_refused(env, route):
    assert fs(client(), route, env.profile / 'config.yaml').status_code == 403


@pytest.mark.parametrize('route', (*FS_READ_ROUTES, 'write-text', 'default-cwd'))
def test_fs_restricted_never_consults_ssh(env, monkeypatch, route):
    def forbidden(profile=None):
        raise AssertionError('Restricted request consulted SSH')
    monkeypatch.setattr('hermes_cli.web_routers.files._fs_backend', forbidden)
    c = client()
    path = env.ws if route in ('list', 'git-root') else env.ws / 'notes.txt'
    response = fs_write(c, path) if route == 'write-text' else fs(c, route, path)
    assert response.status_code == 200


def test_fs_write_and_default_workspace(env):
    c = client()
    assert fs_write(c, env.ws / 'notes.txt').status_code == 200
    assert (env.ws / 'notes.txt').read_text() == 'changed'
    assert fs_write(c, env.profile / 'config.yaml').status_code == 403
    assert (env.profile / 'config.yaml').read_text() == 'secret: 1'
    assert fs(c, 'default-cwd').json() == {'cwd': str(env.ws.resolve()), 'branch': ''}


def test_fs_git_discovery_stops_at_workspace(env, monkeypatch):
    (env.profile / '.git').mkdir()
    c = client()
    assert fs(c, 'git-root', env.ws).json() == {'root': None}
    nested = env.ws / 'repo'
    (nested / '.git').mkdir(parents=True)
    assert fs(c, 'git-root', nested).json() == {'root': str(nested.resolve())}


@pytest.mark.platforms('posix')
def test_fs_symlink_reads_and_writes_remain_inside_workspace(env, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = outside / 'secret.txt'
    target.write_text('untouched')
    (env.ws / 'leaf').symlink_to(target)
    (env.ws / 'parent').symlink_to(outside, target_is_directory=True)
    (env.ws / 'dangling').symlink_to(outside / 'missing.txt')
    c = client()
    for route in FS_READ_ROUTES:
        assert fs(c, route, env.ws / 'leaf').status_code == 403
    for path in ('leaf', 'parent/missing.txt', 'dangling'):
        assert fs_write(c, env.ws / path).status_code == 403
    assert target.read_text() == 'untouched'
    assert not (outside / 'missing.txt').exists()
    (env.ws / 'safe').symlink_to(env.ws / 'notes.txt')
    assert fs(c, 'read-text', env.ws / 'safe').json()['text'] == 'hello'
    assert fs_write(c, env.ws / 'safe').status_code == 200
    assert (env.ws / 'notes.txt').read_text() == 'changed'


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('route', (*FS_READ_ROUTES, 'write-text', 'default-cwd'))
def test_fs_escaped_workspace_root_refused(env, tmp_path, route):
    import shutil
    outside = tmp_path / 'outside'
    outside.mkdir()
    shutil.rmtree(env.ws)
    env.ws.symlink_to(outside, target_is_directory=True)
    c = client()
    response = fs_write(c, env.ws / 'new.txt') if route == 'write-text' else fs(c, route, env.ws)
    assert response.status_code == 403
    assert list(outside.iterdir()) == []
