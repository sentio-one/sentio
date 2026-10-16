"""A federated (dashboard) identity reaches only the profiles it was granted (private fork, phase 2).

Real ``AuthorityConnection`` objects over real per-profile authorities, built the way
``tui_gateway/ws.py`` builds them for the dashboard socket. ``allowed_profiles`` is the server-minted
grant; the local native ticket path (``profile_scope``) keeps its behaviour.
"""
import json
from pathlib import Path

import pytest
import pytest_asyncio

from tests.gateway.test_session_authorities_multiplex import _reserve_homes, _runner


class _Transport:
    def write(self, frame):
        return True

    def close(self):
        pass


def _identity(**extra):
    return {'user_id': 'u-alice', 'provider': 'self_hosted', 'issuer': 'https://sso.corp.example', **extra}


@pytest_asyncio.fixture
async def served(tmp_path, monkeypatch):
    """Default + alpha + beta authorities; yields (root, runner, connect, call)."""
    from gateway.run_runtime import initialize_gateway_runtime
    from gateway.runtime_ownership import process_ownership
    from gateway.session_authorities import owner_scope
    from gateway.session_controls import AuthorityConnection
    from gateway.session import SessionSource
    from gateway.config import Platform
    from gateway.session_authority import LiveSession
    from gateway.session_authorities import authority_for_profile_id

    root, homes = _reserve_homes(tmp_path, monkeypatch)
    process_ownership.reserve([home for _, home in homes])
    runner, connections = _runner(root, homes), []
    await initialize_gateway_runtime(runner)
    for name in ('alpha', 'beta', 'default'):
        home = root if name == 'default' else root / 'profiles' / name
        authority = authority_for_profile_id(runner, str(home.resolve()))
        authority.db.create_session(f'{name}-private', source='api_server')
        authority.db.append_message(f'{name}-private', 'user', f'PRIVATE_{name.upper()}_MARKER')
        authority.sessions[f'{name}-private'] = LiveSession(
            SessionSource(platform=Platform.LOCAL, chat_id='local-x', user_id='uid:other'), 'route')

    def connect(identity, *, operator=False):
        authority = runner.session_authority  # the dashboard socket binds the launch authority
        with owner_scope(authority):
            connection = AuthorityConnection(authority, _Transport(), identity, operator=operator)
        connections.append(connection)
        return connection

    async def call(connection, method, **params):
        with owner_scope(connection.authority):
            return await connection.dispatch({'id': 1, 'method': method, 'params': params})

    try:
        yield root, runner, connect, call
    finally:
        for connection in connections:
            with owner_scope(connection.authority):
                await connection.close()
        for authority in list(getattr(runner, 'session_authorities', []) or []):
            authority.db.close()
        for _, home in homes:
            process_ownership.release(Path(home))


def _refused(reply):
    return reply.get('error', {}).get('message') == 'profile_mismatch'


@pytest.mark.asyncio
async def test_granted_profile_is_reachable_and_nothing_else(served):
    _, _, connect, call = served
    alice = connect(_identity(allowed_profiles=['alpha']))
    # A granted caller reaches the profile, but still sees only sessions it owns inside it.
    assert 'result' in await call(alice, 'session.list', profile='alpha')
    for profile in ('beta', 'gamma'):
        reply = await call(alice, 'session.list', profile=profile)
        assert _refused(reply), (profile, reply)
        assert 'PRIVATE_BETA_MARKER' not in json.dumps(reply)


@pytest.mark.asyncio
async def test_launch_profile_is_not_a_free_default_for_a_non_granted_caller(served):
    """Finding A1: the own-home early return in _sibling_for must not skip the allowlist, and verbs
    without a profile key run on the connection's own authority."""
    _, _, connect, call = served
    alice = connect(_identity(allowed_profiles=['alpha']))
    for params in ({}, {'profile': 'default'}):
        reply = await call(alice, 'session.list', **params)
        assert _refused(reply), (params, reply)
        assert 'PRIVATE_DEFAULT_MARKER' not in json.dumps(reply)
    for method, params in (('session.create', {}), ('session.resume', {'session_id': 'default-private'}),
                           ('prompt.submit', {'session_id': 'default-private', 'submission_id': 'x', 'text': 't'})):
        assert _refused(await call(alice, method, **params)), method
    assert (await call(alice, 'ping'))['result'] == {'pong': True}


@pytest.mark.asyncio
async def test_launch_profile_is_reachable_when_it_is_granted(served):
    _, _, connect, call = served
    bob = connect(_identity(allowed_profiles=['default', 'alpha']))
    assert 'result' in await call(bob, 'session.list')
    assert 'result' in await call(bob, 'session.list', profile='default')
    assert 'result' in await call(bob, 'session.list', profile='alpha')
    assert _refused(await call(bob, 'session.list', profile='beta'))


@pytest.mark.asyncio
@pytest.mark.parametrize('spelling', ['Beta', 'BETA', ' beta', '../profiles/beta', 'profiles/beta'])
async def test_selector_spellings_cannot_dodge_the_allowlist(served, spelling):
    _, _, connect, call = served
    alice = connect(_identity(allowed_profiles=['alpha']))
    reply = await call(alice, 'session.list', profile=spelling)
    assert 'result' not in reply and 'PRIVATE_BETA_MARKER' not in json.dumps(reply), reply


@pytest.mark.asyncio
async def test_empty_grant_reaches_nothing(served):
    _, _, connect, call = served
    nobody = connect(_identity(allowed_profiles=[]))
    for profile in (None, 'default', 'alpha', 'beta'):
        reply = await call(nobody, 'session.list', **({'profile': profile} if profile else {}))
        assert _refused(reply), (profile, reply)
    assert 'result' in await call(nobody, 'ping')


@pytest.mark.asyncio
async def test_host_scoped_identity_still_reaches_every_served_profile(served):
    """Admin and the local single-user dashboard keep today's behaviour: no allowlist, host scope."""
    _, _, connect, call = served
    admin = connect(_identity(profile_scope='host'), operator=True)
    assert [r['session_id'] for r in (await call(admin, 'session.list', profile='beta'))['result']['sessions']] \
        == ['beta-private']
    assert 'result' in await call(admin, 'session.list')
    assert _refused(await call(admin, 'session.list', profile='gamma'))


@pytest.mark.asyncio
async def test_actor_carries_the_grant_and_a_host_scoped_actor_carries_none(served):
    _, _, connect, _ = served
    assert connect(_identity(allowed_profiles=['alpha', 'beta'])).actor.allowed_profiles == {'alpha', 'beta'}
    assert connect(_identity(profile_scope='host')).actor.allowed_profiles is None


@pytest.mark.asyncio
async def test_a_denied_profile_is_logged_with_the_subject(served, caplog):
    _, _, connect, call = served
    alice = connect(_identity(allowed_profiles=['alpha']))
    with caplog.at_level('WARNING'):
        await call(alice, 'session.list', profile='beta')
    assert any('u-alice' in r.getMessage() and 'beta' in r.getMessage() for r in caplog.records)
