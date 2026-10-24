"""The legacy RPC fallback honours a grant-scoped caller's profile allowlist (private fork, phase 3).

``dispatch_legacy`` runs the sidecar verbs the session authority does not own (config, profiles,
connectors, ...). Before this, an explicit ``profile`` param was resolved with no authorization, so a
socket that was correctly confined on the authority plane could still read or write any profile
through the fallback. Grant-scoped actors now get the same allowlist here, host-level verbs are
admin-only, and the single resolver ``_profile_home`` refuses an ungranted profile as a backstop.
"""
from dataclasses import replace
from pathlib import Path
import threading

import pytest

from gateway.session_contract import Principal
from tui_gateway.ws_legacy_fallback import ADMIN_ONLY_METHODS, dispatch_legacy, legacy_fallback_allowed


def _caps():
    from gateway.runtime_bootstrap import _PURPOSE_CAPABILITIES
    return frozenset(_PURPOSE_CAPABILITIES['interactive'])


def _actor(launch: Path, allowed):
    return Principal('auth:v1:["self-hosted","https://sso.corp.example","u-alice"]', str(launch), _caps(), 't',
                     None if allowed is None else frozenset(allowed))


class _Server:
    """The slice of tui_gateway.server that dispatch_legacy touches."""

    def __init__(self, root: Path):
        self.root, self.calls, self._sessions = root, [], {}
        self._sessions_lock = threading.Lock()

    def _launch_home(self):
        return str(self.root)

    def _profile_home(self, name):
        name = (name or '').strip()
        if not name or name == 'default':
            return None
        home = self.root / 'profiles' / name
        if not home.is_dir():
            raise FileNotFoundError(name)
        return home

    def dispatch(self, req, transport):
        self.calls.append(req)
        return {'jsonrpc': '2.0', 'id': req.get('id'), 'result': {'ok': True}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / '.hermes'
    for name in ('alpha', 'beta'):
        (root / 'profiles' / name).mkdir(parents=True)
        (root / 'profiles' / name / 'config.yaml').write_text('{}')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(root))
    return root, _Server(root)


def _call(server, actor, method='config.get', **params):
    return dispatch_legacy(server, {'id': 1, 'method': method, 'params': params}, object(), actor)


def _refused(reply):
    return reply.get('error', {}).get('code') == 4064


def test_an_ungranted_explicit_profile_is_refused_before_any_handler_runs(world):
    root, server = world
    alice = _actor(root, ['alpha'])
    for profile in ('beta', 'BETA', ' beta ', 'nope', '../x', 'default'):
        assert _refused(_call(server, alice, profile=profile)), profile
    assert server.calls == []


def test_a_granted_explicit_profile_reaches_the_handler(world):
    root, server = world
    assert 'result' in _call(server, _actor(root, ['alpha']), profile='alpha')
    assert 'result' in _call(server, _actor(root, ['alpha']), profile='Alpha')
    assert len(server.calls) == 2


def test_profile_less_verbs_run_on_the_launch_profile_so_it_must_be_granted(world):
    root, server = world
    assert _refused(_call(server, _actor(root, ['alpha'])))
    assert 'result' in _call(server, _actor(root, ['default']))


def test_a_session_bound_verb_is_checked_against_that_sessions_profile(world):
    root, server = world
    server._sessions['s-beta'] = {'profile_home': str(root / 'profiles' / 'beta')}
    assert _refused(_call(server, _actor(root, ['default', 'alpha']), session_id='s-beta'))
    assert 'result' in _call(server, _actor(root, ['beta']), session_id='s-beta')


def test_a_host_scoped_actor_is_not_restricted(world):
    root, server = world
    admin = _actor(root, None)
    for params in ({}, {'profile': 'beta'}, {'profile': 'alpha'}):
        assert 'result' in _call(server, admin, **params)


def test_capability_handshakes_are_not_blocked_for_a_caller_without_the_launch_profile(world):
    root, server = world
    assert 'result' in _call(server, _actor(root, ['alpha']), method='client.capabilities')


def test_host_level_verbs_are_admin_only():
    scoped, admin = _actor(Path('/x'), ['alpha']), _actor(Path('/x'), None)
    assert {'profiles.create', 'display.install', 'diagnostics.share_nous', 'machine.facts'} <= ADMIN_ONLY_METHODS
    for method in ('profiles.create', 'display.install', 'wake.start', 'machine.facts'):
        assert legacy_fallback_allowed(scoped, method) is False, method
        assert legacy_fallback_allowed(admin, method) is True, method
    assert legacy_fallback_allowed(scoped, 'config.get') is True


def test_store_wide_live_listing_is_not_served_to_a_grant_scoped_actor():
    scoped = _actor(Path('/x'), ['alpha'])
    assert legacy_fallback_allowed(scoped, 'session.active_list') is False
    assert legacy_fallback_allowed(replace(scoped, allowed_profiles=None), 'session.active_list') is True


def test_the_profile_resolver_refuses_an_ungranted_home_as_a_backstop(tmp_path, monkeypatch):
    from tui_gateway.ws_legacy_fallback import granted_profiles_scope
    from tui_gateway import server

    root = tmp_path / '.hermes'
    for name in ('alpha', 'beta'):
        (root / 'profiles' / name).mkdir(parents=True)
        (root / 'profiles' / name / 'config.yaml').write_text('{}')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(root))
    assert server._profile_home('beta') is not None  # no scope bound: unchanged
    with granted_profiles_scope(frozenset({'alpha'})):
        assert server._profile_home('alpha') is not None
        for name in ('beta', 'default', None):
            with pytest.raises(server.ProfileUnavailableError):
                server._profile_home(name)
    assert server._profile_home('beta') is not None  # the scope does not leak past the request


def test_the_legacy_roster_is_filtered_to_the_grant(tmp_path, monkeypatch):
    from tui_gateway import server
    from tui_gateway.ws_legacy_fallback import granted_profiles_scope

    root = tmp_path / '.hermes'
    for name in ('alpha', 'beta'):
        (root / 'profiles' / name).mkdir(parents=True)
        (root / 'profiles' / name / 'config.yaml').write_text('{}')
    (root / 'config.yaml').write_text('{}')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(root))

    def names():
        reply = server.handle_request({'jsonrpc': '2.0', 'id': 1, 'method': 'profiles.list',
                                 'params': {'include_sessions': False}})
        return sorted(row['name'] for row in reply['result']['profiles'])

    assert names() == ['alpha', 'beta', 'default']
    with granted_profiles_scope(frozenset({'alpha'})):
        assert names() == ['alpha']
    with granted_profiles_scope(frozenset()):
        assert names() == []


def test_the_roster_verb_is_not_refused_when_the_launch_profile_is_not_granted(world):
    root, server = world
    assert 'result' in _call(server, _actor(root, ['alpha']), method='profiles.list')
