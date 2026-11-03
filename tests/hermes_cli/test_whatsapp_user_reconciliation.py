"""Shared-user mutations must agree with the persisted gateway admission policy."""
from pathlib import Path

import pytest

from gateway.config import load_gateway_config
from hermes_cli import whatsapp_users as wu
from hermes_cli.config import atomic_config_write, load_env, read_user_config_raw, save_env_value

OWNER = '27820000001'
NUMBER = '27820000002'


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / '.hermes'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.delenv(wu.ALLOWLIST_ENV, raising=False)
    return home


@pytest.mark.parametrize('shape', ['top', 'nested', 'conflicting', 'null-top'])
def test_mutations_agree_with_real_gateway_loader(home, shape):
    data = {'gateway': {'owners': [OWNER], 'profile_routes': []}}
    if shape in {'top', 'conflicting'}:
        data.update(owners=[OWNER], profile_routes=[])
        data['gateway'] = {} if shape == 'top' else {
            'owners': [NUMBER],
            'profile_routes': [{'platform': 'whatsapp', 'profile': 'other', 'chat_id': NUMBER}],
        }
    elif shape == 'null-top':
        data.update(owners=None, profile_routes=None)
    atomic_config_write(home / 'config.yaml', data)
    wu.add_user('sales', NUMBER)
    runtime = load_gateway_config()
    assert runtime.owners == [OWNER]
    assert [(r.chat_id, r.profile) for r in runtime.profile_routes] == [(NUMBER, 'sales')]
    assert {u.number for u in wu.list_users()} == {OWNER, NUMBER}
    wu.remove_user(NUMBER)
    assert load_gateway_config().profile_routes == []
    raw = read_user_config_raw(home / 'config.yaml')
    if shape in {'top', 'conflicting'}:
        assert raw['profile_routes'] == []


def test_empty_authoritative_owners_refuses_before_writes(home):
    data = {'owners': [], 'gateway': {'owners': [OWNER]}}
    atomic_config_write(home / 'config.yaml', data)
    with pytest.raises(wu.WhatsAppUsersError, match='owners'):
        wu.add_user('sales', NUMBER)
    assert read_user_config_raw(home / 'config.yaml') == data
    assert not (home / 'profiles/sales').exists()


def test_repeat_add_repairs_persisted_grant_without_recreating_profile(home):
    atomic_config_write(home / 'config.yaml', {'gateway': {'owners': [OWNER]}})
    user = wu.add_user('sales', NUMBER)
    marker = home / 'profiles/sales/SOUL.md'
    marker.write_text('keep me')
    save_env_value(wu.ALLOWLIST_ENV, OWNER)
    assert wu.add_user('sales', NUMBER + '@s.whatsapp.net') == user
    assert set(load_env()[wu.ALLOWLIST_ENV].split(',')) == {OWNER, NUMBER}
    assert marker.read_text() == 'keep me'
    assert len(load_gateway_config().profile_routes) == 1


def test_repeat_add_rolls_back_failed_grant_repair(home, monkeypatch):
    atomic_config_write(home / 'config.yaml', {'gateway': {'owners': [OWNER]}})
    wu.add_user('sales', NUMBER)
    save_env_value(wu.ALLOWLIST_ENV, OWNER)
    before = read_user_config_raw(home / 'config.yaml')
    allow = wu._allow_number

    def fail(number):
        allow(number)
        raise OSError('grant failed after writing')

    monkeypatch.setattr(wu, '_allow_number', fail)
    with pytest.raises(OSError, match='grant failed'):
        wu.add_user('sales', NUMBER)
    assert load_env()[wu.ALLOWLIST_ENV] == OWNER
    assert read_user_config_raw(home / 'config.yaml') == before
