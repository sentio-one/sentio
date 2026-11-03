"""Shared-number REST lifecycle and authorization through the production dashboard."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gateway.access_policy import parse_access_policy
from hermes_cli.config import atomic_config_write
from hermes_cli.dashboard_auth.registry import clear_providers, register_provider
from tests.hermes_cli.test_dashboard_rest_access import _GroupStub, _call, POLICY


@pytest.fixture
def client(tmp_path, monkeypatch):
    from hermes_cli import web_server

    home = tmp_path / '.hermes'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.delenv('WHATSAPP_ALLOWED_USERS', raising=False)
    atomic_config_write(home / 'config.yaml', {'gateway': {'owners': ['+27820000001']}})
    monkeypatch.setattr(web_server.app.state, 'auth_required', True, raising=False)
    monkeypatch.setattr(web_server.app.state, 'bound_host', '127.0.0.1', raising=False)
    monkeypatch.setattr(web_server.app.state, 'gateway_runner',
                        SimpleNamespace(config=SimpleNamespace(access=POLICY)), raising=False)
    clear_providers()
    register_provider(_GroupStub())
    yield TestClient(web_server.app, base_url='http://127.0.0.1')
    clear_providers()


def test_admin_round_trip_retains_profile_unless_requested(client, tmp_path):
    def call(method, path, **kwargs):
        return _call(client, ['hermes-admins'], method, path, **kwargs)

    created = call('POST', '/api/whatsapp/users', json={'name': 'sentio-sales', 'number': '+27820000002'})
    assert created.status_code == 200
    assert created.json() == {'number': '27820000002', 'profile': 'sentio-sales', 'status': 'live'}
    assert call('GET', '/api/whatsapp/users').json()['users'] == [
        {'number': '27820000001', 'profile': 'default', 'status': 'owner'}, created.json()]
    assert call('DELETE', '/api/whatsapp/users/sentio-sales').status_code == 200
    profile = tmp_path / '.hermes/profiles/sentio-sales'
    assert (profile / 'SOUL.md').exists()
    assert call('GET', '/api/whatsapp/users').json()['users'] == [
        {'number': '27820000001', 'profile': 'default', 'status': 'owner'}]
    assert call('POST', '/api/whatsapp/users', json={'name': 'sentio-sales', 'number': '+27820000002'}).status_code == 200
    assert call('DELETE', '/api/whatsapp/users/27820000002?delete_profile=true').status_code == 200
    assert not profile.exists()


def test_refusals_include_service_reason(client):
    bad = _call(client, ['hermes-admins'], 'POST', '/api/whatsapp/users',
                json={'name': 'sentio-sales', 'number': 'nope'})
    assert bad.status_code == 400
    assert 'phone number' in bad.json()['detail']
    assert _call(client, ['hermes-admins'], 'DELETE', '/api/whatsapp/users/ghost').status_code == 404


@pytest.mark.parametrize('method,path', [('GET', '/api/whatsapp/users'),
    ('POST', '/api/whatsapp/users'), ('DELETE', '/api/whatsapp/users/sentio-sales')])
def test_production_gate_denies_non_admins_and_anonymous(client, method, path):
    for groups in ([], ['hermes-profile-default'], ['hermes-profile-alpha']):
        assert _call(client, groups, method, path + '?profile=default',
                     json={'name': 'sentio-sales', 'number': '+27820000002'}).status_code == 403
    assert client.request(method, path).status_code == 401


def test_no_policy_preserves_signed_in_dashboard_access(client, monkeypatch):
    monkeypatch.setattr(client.app.state.gateway_runner.config, 'access', parse_access_policy(None))
    assert _call(client, [], 'GET', '/api/whatsapp/users').status_code == 200
    assert _call(client, [], 'POST', '/api/whatsapp/users',
                 json={'name': 'sentio-sales', 'number': '+27820000002'}).status_code == 200
    assert _call(client, [], 'DELETE', '/api/whatsapp/users/sentio-sales').status_code == 200
