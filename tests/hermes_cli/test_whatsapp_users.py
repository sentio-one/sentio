from pathlib import Path
import pytest
from hermes_cli import whatsapp_users as wu
from hermes_cli.config import atomic_config_write, read_user_config_raw, save_env_value, get_env_value

OWNER = '27820000001'
NUMBER = '27820000002'

@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / '.hermes'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.delenv('WHATSAPP_ALLOWED_USERS', raising=False)
    atomic_config_write(home / 'config.yaml', {'gateway': {'owners': [OWNER]}})
    return home

def config(home):
    return read_user_config_raw(home / 'config.yaml')

def route(**extra):
    return dict(name='person', platform='whatsapp', profile='sales', chat_id=NUMBER, **extra)

def test_lifecycle_and_exact_repeat(home):
    user = wu.add_user('sales', '+' + NUMBER)
    assert user == wu.WhatsAppUser(NUMBER, 'sales', 'live')
    save_env_value('WHATSAPP_ALLOWED_USERS', '')
    before = config(home), (home / '.env').read_bytes()
    assert wu.add_user('sales', NUMBER + '@s.whatsapp.net') == user
    assert (config(home), (home / '.env').read_bytes()) == before
    assert wu.WhatsAppUser(OWNER, 'default', 'owner') in wu.list_users()
    assert wu.remove_user(NUMBER).profile == 'sales'
    assert (home / 'profiles/sales/SOUL.md').exists()

@pytest.mark.parametrize('number', ['', 'abc', '12', '123@g.us', 'status@broadcast', OWNER])
def test_invalid_numbers(home, number):
    with pytest.raises(wu.WhatsAppUsersError):
        wu.add_user('sales', number)
    assert not (home / 'profiles/sales').exists()

@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('seam', ['_allow_number', '_write_routes'])
def test_write_then_raise_restores_exact_state(home, monkeypatch, existing, seam):
    from hermes_cli.profiles import create_profile
    if existing:
        create_profile('sales', no_alias=True)
    data = config(home)
    data['gateway']['profile_routes'] = [dict(name='whatsapp-sales', platform='telegram', profile='other')]
    atomic_config_write(home / 'config.yaml', data)
    save_env_value('WHATSAPP_ALLOWED_USERS', '27829999999')
    before = config(home), get_env_value('WHATSAPP_ALLOWED_USERS')
    original = getattr(wu, seam)
    def fail(*args):
        original(*args)
        raise RuntimeError('after persistence')
    monkeypatch.setattr(wu, seam, fail)
    with pytest.raises(RuntimeError, match='after persistence'):
        wu.add_user('sales', NUMBER)
    assert (config(home), get_env_value('WHATSAPP_ALLOWED_USERS')) == before
    assert (home / 'profiles/sales').exists() == existing

@pytest.mark.parametrize('extra', [{'bot_profile': 'secondary'}, {'chat_id': '123@g.us'}])
def test_unrelated_routes_preserved(home, extra):
    row = route()
    row.update(extra)
    data = config(home)
    data['gateway']['profile_routes'] = [row]
    atomic_config_write(home / 'config.yaml', data)
    wu.add_user('sales', NUMBER)
    wu.remove_user(NUMBER)
    assert config(home)['gateway']['profile_routes'] == [row]

@pytest.mark.parametrize('rows', [[route(enabled=False)], [route(user_id=NUMBER)], [route(), route()]])
def test_ambiguous_routes_refused(home, rows):
    data = config(home)
    data['gateway']['profile_routes'] = rows
    atomic_config_write(home / 'config.yaml', data)
    with pytest.raises(wu.WhatsAppUsersError):
        wu.add_user('sales', NUMBER)
    with pytest.raises(wu.WhatsAppUsersError):
        wu.remove_user('sales')
    assert config(home)['gateway']['profile_routes'] == rows

def test_parked_missing_and_delete(home):
    import shutil
    wu.add_user('sales', NUMBER)
    profile = home / 'profiles/sales'
    (profile / 'gateway.parked').touch()
    assert wu.add_user('sales', NUMBER).status == 'parked'
    assert wu.list_users()[-1].status == 'parked'
    shutil.rmtree(profile)
    assert wu.list_users()[-1].status == 'missing'
    wu.remove_user('sales')
    wu.add_user('sales', NUMBER)
    assert wu.remove_user('sales', delete_profile=True).status == 'missing'
    assert not profile.exists()


def test_access_policy_stamps_real_sandbox_config_without_keys(home):
    data = config(home)
    data['access'] = {'admin_emails': ['operator@example.com']}
    atomic_config_write(home / 'config.yaml', data)
    wu.add_user('sales', NUMBER)
    profile = home / 'profiles/sales'
    assert read_user_config_raw(profile / 'config.yaml')['terminal']['backend'] == 'docker'
    assert 'API_KEY=' not in (profile / '.env').read_text()


def test_concurrent_adds_do_not_lose_routes(home):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(wu.add_user, name, number) for name, number in
                   [('sales', NUMBER), ('support', '27820000003')]]
        assert all(f.result().status == 'live' for f in futures)
    assert {u.profile for u in wu.list_users()} == {'default', 'sales', 'support'}


def test_remove_write_then_raise_restores_routes_and_allowlist(home, monkeypatch):
    wu.add_user('sales', NUMBER)
    before = config(home), get_env_value('WHATSAPP_ALLOWED_USERS')
    original = wu._disallow_number
    def fail(number):
        original(number)
        raise RuntimeError('after removal')
    monkeypatch.setattr(wu, '_disallow_number', fail)
    with pytest.raises(RuntimeError, match='after removal'):
        wu.remove_user('sales')
    assert (config(home), get_env_value('WHATSAPP_ALLOWED_USERS')) == before

@pytest.mark.parametrize('selector', ['user_id', 'lid'])
def test_production_route_collision_refused(home, selector):
    import json
    from gateway.profile_routing import parse_profile_routes, match_profile_route
    jid = NUMBER + '@s.whatsapp.net'
    row = dict(name='existing', platform='whatsapp', profile='other')
    if selector == 'user_id':
        row['user_id'] = jid
    else:
        session = home / 'whatsapp/session'
        session.mkdir(parents=True)
        (session / ('lid-mapping-' + NUMBER + '.json')).write_text(json.dumps('555666777'))
        row['chat_id'] = '555666777@lid'
    data = config(home)
    data['gateway']['profile_routes'] = [row]
    atomic_config_write(home / 'config.yaml', data)
    resolved = match_profile_route(parse_profile_routes([row]), 'whatsapp', chat_id=jid, user_id=jid)
    assert resolved.profile == 'other'
    before = config(home)
    with pytest.raises(wu.WhatsAppUsersError):
        wu.add_user('sales', NUMBER)
    assert config(home) == before
    assert not (home / 'profiles/sales').exists()


def test_second_number_for_same_profile_refused_without_changes(home):
    wu.add_user('sales', NUMBER)
    before = config(home), (home / '.env').read_bytes()
    with pytest.raises(wu.WhatsAppUsersError):
        wu.add_user('sales', '27820000003')
    assert (config(home), (home / '.env').read_bytes()) == before
    assert wu.remove_user('sales').number == NUMBER


def test_inherited_stale_environment_does_not_overwrite_persisted_allowlist(home):
    import os
    import subprocess
    import sys
    from hermes_cli.config import load_env
    save_env_value(wu.ALLOWLIST_ENV, OWNER)
    inherited = dict(os.environ)
    inherited[wu.ALLOWLIST_ENV] = OWNER
    wu.add_user('sales', NUMBER)
    script = '''
from pathlib import Path
import sys
Path.home = lambda: Path(sys.argv[1])
from hermes_cli.whatsapp_users import add_user
add_user('support', '27820000003')
'''
    result = subprocess.run([sys.executable, '-c', script, str(home.parent)],
                            env=inherited, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert set(load_env()[wu.ALLOWLIST_ENV].split(',')) == {OWNER, NUMBER, '27820000003'}


@pytest.mark.parametrize('persisted', [None, '', OWNER])
def test_rollback_uses_persisted_presence_not_environment(home, monkeypatch, persisted):
    from hermes_cli.config import load_env
    if persisted is not None:
        save_env_value(wu.ALLOWLIST_ENV, persisted)
    monkeypatch.setenv(wu.ALLOWLIST_ENV, '27829999999')
    original = wu._allow_number
    def fail(number):
        original(number)
        raise RuntimeError('after write')
    monkeypatch.setattr(wu, '_allow_number', fail)
    with pytest.raises(RuntimeError, match='after write'):
        wu.add_user('sales', NUMBER)
    assert load_env().get(wu.ALLOWLIST_ENV) == persisted
    assert (wu.ALLOWLIST_ENV in load_env()) == (persisted is not None)


def test_cleanup_failure_preserves_original_and_attempts_remaining_cleanup(home, monkeypatch):
    from hermes_cli.config import load_env
    original = wu._allow_number
    def fail(number):
        original(number)
        raise RuntimeError('initiating failure')
    def cleanup_fail(*args):
        raise LookupError('config cleanup failure')
    monkeypatch.setattr(wu, '_allow_number', fail)
    monkeypatch.setattr(wu, 'atomic_config_replace', cleanup_fail)
    with pytest.raises(RuntimeError, match='initiating failure') as caught:
        wu.add_user('sales', NUMBER)
    assert any('config cleanup failure' in note for note in caught.value.__notes__)
    assert wu.ALLOWLIST_ENV not in load_env()
    assert not (home / 'profiles/sales').exists()
