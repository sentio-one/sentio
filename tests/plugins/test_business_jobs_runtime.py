"""Durable plugin requests use real scoped context and SQLite, including cursor replay."""
import pytest

from gateway.business.config import load_business_config
from gateway.business.models import Principal, Scope
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


def service_at(tmp_path):
    service = BusinessService(BusinessStore(tmp_path), config_resolver=load_business_config({'mode':'shadow'}, supervised_latch=False))
    service.account_scope = Scope('default', 'default', 'default', '')
    return service


def test_real_context_records_idempotent_job_and_refuses_offline(tmp_path, monkeypatch):
    from gateway.business.jobs import handle_plugin_request
    from gateway.business import client
    service = service_at(tmp_path)
    principal = Principal('plugin:sales', 'default', frozenset({'submit_job'}), 'plugin')
    calls = []
    def request(home, *, operation, params, **kwargs):
        calls.append(operation)
        return handle_plugin_request(service, operation.removeprefix('plugin.'), params, principal)
    monkeypatch.setattr(client, 'request_business', request)
    context = PluginContext(PluginManifest(name='sales', version='1.0'), PluginManager())
    request_params = dict(contact='27800000001@s.whatsapp.net', intent='outreach', request_id='request-1')
    first = context.business_request('submit_job', request_params)
    assert first['ok'] and first['state'] == 'held'
    assert context.business_request('submit_job', request_params)['job_id'] == first['job_id']
    with service.store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM business_plugin_jobs').fetchone()[0] == 1
    request_params['intent'] = 'changed intent'
    assert context.business_request('submit_job', request_params)['error'] == 'request_payload_conflict'
    assert calls


def test_durable_facts_actions_and_foreign_scope_denial(tmp_path):
    from gateway.business.jobs import handle_plugin_request
    from gateway.business.store_jobs import context_contributions
    service = service_at(tmp_path)
    principal = Principal('plugin:sales', 'default', frozenset({'record_fact','record_action'}), 'plugin')
    params = dict(contact='27800000001@s.whatsapp.net', id='price', text='R50', source='catalog', version='1')
    result = handle_plugin_request(service, 'record_fact', params, principal)
    assert result['ok']
    scope = Scope(**result['scope'])
    handle_plugin_request(service, 'record_action', dict(contact=params['contact'], id='demo', kind='demo', subject='example', state='completed', proof_ref='crm:1'), principal)
    facts, actions = context_contributions(BusinessStore(tmp_path), scope)
    assert facts[0].text == 'R50'
    assert actions[0].proof_ref == 'crm:1'
    assert handle_plugin_request(service, 'record_fact', dict(params,runtime_profile='other'), principal)['error'] == 'permission_denied'


@pytest.mark.asyncio
async def test_worker_reviews_submitted_candidate_and_promotes_real_outbox(tmp_path, monkeypatch):
    from gateway.business.jobs import handle_plugin_request, process_jobs
    from gateway.business import review
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import get_plugin_manager
    import hermes_yaml as yaml
    home = tmp_path / '.hermes'; home.mkdir()
    monkeypatch.setenv('HOME',str(tmp_path))
    monkeypatch.setenv('HERMES_HOME',str(home))
    plugin = home / 'plugins' / 'sales_policy'; plugin.mkdir(parents=True)
    (plugin/'plugin.yaml').write_text(yaml.safe_dump({'name':'sales_policy','version':'1'}))
    (plugin/'__init__.py').write_text('''
def register(ctx):
    ctx.register_hook('business_context', lambda **kwargs: dict(version='v1',facts=[],actions=[],rules=[],criteria=[],missing=[]))
    ctx.register_hook('business_check', lambda **kwargs: dict(version='v1',issues=[]))
''')
    (home/'config.yaml').write_text(yaml.safe_dump({'plugins':{'enabled':['sales_policy']}}))
    bundled = tmp_path/'bundled'; bundled.mkdir()
    monkeypatch.setattr(plugins_mod,'get_bundled_plugins_dir',lambda:bundled)
    manager = get_plugin_manager(); manager.discover_and_load()
    cfg = load_business_config({'mode':'supervised','required_policies':['sales_policy']},supervised_latch=True)
    service = BusinessService(BusinessStore(home),config_resolver=cfg)
    principal = Principal('plugin:sales','default',frozenset({'submit_candidate'}),'plugin')
    result = handle_plugin_request(service,'submit_candidate',{'request_id':'candidate-one','contact':'27800000001@s.whatsapp.net','text':'Exact submitted text'},principal)
    assert result['ok']
    async def llm(**kwargs):
        assert kwargs['task'] == 'business_review'
        return {'recommendation':'approve','reasons':[],'policy_refs':[]}
    monkeypatch.setattr(review,'async_call_llm',llm)
    try:
        await process_jobs(service)
        with service.store._connect() as db:
            job = db.execute('SELECT * FROM business_plugin_jobs').fetchone()
            outbox = db.execute('SELECT * FROM business_outbox').fetchone()
            assert job['state'] == 'drafted'
            assert outbox['state'] == 'held'
            assert outbox['request_id'] == 'candidate-one'
        scope = Scope(**result['scope'])
        assert service.store.get_revision(scope,job['revision_id']).candidate.text == 'Exact submitted text'
        await process_jobs(service)
        with service.store._connect() as db:
            assert db.execute('SELECT COUNT(*) FROM business_revisions').fetchone()[0] == 1
    finally:
        manager.unload()
