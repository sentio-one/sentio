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
