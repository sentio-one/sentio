"""Profile names must not transfer static routes to a later incarnation."""
import json
from pathlib import Path

import pytest

from gateway.config import load_gateway_config
from hermes_cli import profiles
from hermes_cli.config import atomic_config_replace, atomic_config_write, read_user_config_raw


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / '.hermes'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    for name in ('_cleanup_gateway_service', '_maybe_unregister_gateway_service',
                 '_maybe_register_gateway_service', '_stop_profile_backends'):
        monkeypatch.setattr(profiles, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(profiles, 'check_alias_collision', lambda name: 'skip wrapper')
    profiles.create_profile('sales', no_alias=True)
    routes = [
        {'platform': 'whatsapp', 'profile': 'sales', 'chat_id': '27820000002'},
        {'platform': 'telegram', 'profile': 'other', 'bot_profile': 'sales', 'chat_id': '123'},
        {'platform': 'whatsapp', 'profile': 'other', 'chat_id': '27820000003'},
    ]
    atomic_config_write(home / 'config.yaml', {'profile_routes': routes, 'gateway': {'profile_routes': routes}})
    return home


def test_delete_revokes_routes_before_name_can_be_reused(home):
    profiles.delete_profile('sales', yes=True)
    profiles.create_profile('sales', no_alias=True)
    runtime = load_gateway_config()
    assert [(r.profile, r.bot_profile) for r in runtime.profile_routes] == [('other', None)]
    raw = read_user_config_raw(home / 'config.yaml')
    assert raw['profile_routes'] == raw['gateway']['profile_routes']


def test_rename_moves_target_and_bot_routes_and_old_name_can_be_reused(home):
    profiles.rename_profile('sales', 'renamed')
    profiles.create_profile('sales', no_alias=True)
    runtime = load_gateway_config()
    assert [(r.profile, r.bot_profile) for r in runtime.profile_routes] == [
        ('renamed', None), ('other', 'renamed'), ('other', None),
    ]
    raw = read_user_config_raw(home / 'config.yaml')
    assert raw['profile_routes'] == raw['gateway']['profile_routes']


def test_failed_rename_restores_routes_and_original_directory(home, monkeypatch):
    original = read_user_config_raw(home / 'config.yaml')
    rename = Path.rename

    def fail(path, target):
        if path == home / 'profiles/sales':
            raise OSError('directory busy')
        return rename(path, target)

    monkeypatch.setattr(Path, 'rename', fail)
    with pytest.raises(OSError, match='directory busy'):
        profiles.rename_profile('sales', 'renamed')
    assert (home / 'profiles/sales').is_dir()
    assert not (home / 'profiles/renamed').exists()
    assert read_user_config_raw(home / 'config.yaml') == original


def test_failed_delete_does_not_restore_revoked_routes(home, monkeypatch):
    def fail(*args):
        raise OSError('directory busy')

    monkeypatch.setattr(profiles, '_rmtree_with_retry', fail)
    with pytest.raises(RuntimeError, match='directory busy'):
        profiles.delete_profile('sales', yes=True)
    assert all(r.profile != 'sales' and r.bot_profile != 'sales'
               for r in load_gateway_config().profile_routes)


@pytest.mark.parametrize('action', ['delete', 'rename'])
def test_legacy_routes_are_overridden_before_profile_name_is_reused(home, action):
    raw = read_user_config_raw(home / 'config.yaml')
    (home / 'gateway.json').write_text(json.dumps({'profile_routes': raw['profile_routes']}))
    atomic_config_replace(home / 'config.yaml', {})
    if action == 'delete':
        profiles.delete_profile('sales', yes=True)
    else:
        profiles.rename_profile('sales', 'renamed')
    profiles.create_profile('sales', no_alias=True)
    routes = load_gateway_config().profile_routes
    assert all(r.profile != 'sales' and r.bot_profile != 'sales' for r in routes)
    assert any(r.profile == 'renamed' for r in routes) is (action == 'rename')


def test_administrator_routes_block_name_reuse_until_policy_is_updated(home, tmp_path, monkeypatch):
    managed = tmp_path / 'managed'
    managed.mkdir()
    atomic_config_write(managed / 'config.yaml', {
        'profile_routes': [{'platform': 'whatsapp', 'profile': 'sales', 'chat_id': '27820000002'}],
    })
    monkeypatch.setenv('HERMES_MANAGED_DIR', str(managed))
    original = read_user_config_raw(home / 'config.yaml')
    with pytest.raises(ValueError, match='managed'):
        profiles.delete_profile('sales', yes=True)
    assert profiles.profile_exists('sales')
    assert read_user_config_raw(home / 'config.yaml') == original
