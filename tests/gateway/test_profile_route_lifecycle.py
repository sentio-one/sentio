"""Static route lifecycle changes reach a running gateway before a name is reused."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.config import Platform, load_gateway_config
from gateway.control_socket import GatewayControlServer, query_gateway_control
from gateway.pairing import PairingStore
from gateway.profile_route_lifecycle import reload_profile_routes_verb
from gateway.run import GatewayRunner
from gateway.session_identity import resolve_identity
from hermes_cli import profiles
from hermes_cli.config import atomic_config_write, read_user_config_raw
from tests.gateway.test_whatsapp_shared_number_routing import _stub

NUMBER = '27820000002'


@pytest.fixture
def live(tmp_path, monkeypatch):
    home = tmp_path / '.hermes'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('GATEWAY_ALLOW_ALL_USERS', 'true')
    for name in ('_cleanup_gateway_service', '_maybe_unregister_gateway_service',
                 '_maybe_register_gateway_service', '_stop_profile_backends', '_notify_multiplexer'):
        monkeypatch.setattr(profiles, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(profiles, 'check_alias_collision', lambda name: 'skip wrapper')
    profiles.create_profile('sales', no_alias=True)
    atomic_config_write(home / 'config.yaml', {'gateway': {
        'multiplex_profiles': True, 'owners': ['27820000001'],
        'profile_routes': [{'platform': 'whatsapp', 'profile': 'sales', 'chat_id': NUMBER}],
    }})
    runner = object.__new__(GatewayRunner)
    runner.config = load_gateway_config()
    runner._primary_profile_name = 'default'
    runner.pairing_store, runner.pairing_stores = PairingStore(profile='default'), {}
    adapter = _stub(runner)
    runner.adapters, runner._profile_adapters = {Platform('whatsapp'): adapter}, {}
    server = GatewayControlServer(home, verb_handlers={
        'reload-profile-routes': reload_profile_routes_verb(runner),
    })

    def query(home, verb):
        response = json.loads(server.handle_request_line(json.dumps({'verb': verb}).encode()))
        return response.get('result') if response['ok'] else None

    monkeypatch.setattr('gateway.control_socket.query_gateway_control', query)
    monkeypatch.setattr('hermes_cli.gateway_multiplex_served.live_default_gateway_pid', lambda: 123)
    return SimpleNamespace(home=home, runner=runner, adapter=adapter)


def _source(live):
    jid = NUMBER + '@s.whatsapp.net'
    source = live.adapter.build_source(chat_id=jid, chat_type='dm', user_id=jid)
    return source, resolve_identity(source, runner=live.runner)


@pytest.mark.parametrize('action', ['delete', 'rename'])
def test_gateway_never_routes_old_sender_into_replacement_profile(live, action):
    assert _source(live)[1].runtime_profile == 'sales'
    if action == 'delete':
        profiles.delete_profile('sales', yes=True)
    else:
        profiles.rename_profile('sales', 'renamed')
    profiles.create_profile('sales', no_alias=True)
    source, identity = _source(live)
    assert identity.runtime_profile == ('default' if action == 'delete' else 'renamed')
    assert live.runner._is_user_authorized(source) is (action == 'rename')


def test_unresponsive_gateway_blocks_delete_before_name_is_reusable(live, monkeypatch):
    original = read_user_config_raw(live.home / 'config.yaml')
    monkeypatch.setattr('gateway.control_socket.query_gateway_control', lambda *args: None)
    with pytest.raises(RuntimeError, match='Restart it'):
        profiles.delete_profile('sales', yes=True)
    assert profiles.profile_exists('sales')
    assert read_user_config_raw(live.home / 'config.yaml') == original
    assert _source(live)[1].runtime_profile == 'sales'


def test_remove_user_delete_also_revokes_the_gateways_cached_route(live):
    from hermes_cli.whatsapp_users import remove_user
    remove_user('sales', delete_profile=True)
    profiles.create_profile('sales', no_alias=True)
    source, identity = _source(live)
    assert identity.runtime_profile == 'default'
    assert live.runner._is_user_authorized(source) is False


@pytest.mark.asyncio
@pytest.mark.platforms('any')
async def test_lifecycle_reload_over_native_control_transport(live, monkeypatch):
    monkeypatch.setattr('gateway.control_socket.query_gateway_control', query_gateway_control)
    server = GatewayControlServer(live.home, verb_handlers={
        'reload-profile-routes': reload_profile_routes_verb(live.runner),
    })
    assert await server.start()
    try:
        await asyncio.to_thread(profiles.delete_profile, 'sales', yes=True)
        await asyncio.to_thread(profiles.create_profile, 'sales', no_alias=True)
        source, identity = _source(live)
        assert identity.runtime_profile == 'default'
        assert live.runner._is_user_authorized(source) is False
    finally:
        await server.stop()
