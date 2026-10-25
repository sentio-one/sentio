"""Native requests traverse the actual isolated host and authenticated local socket."""
import asyncio
import json
import os

import pytest
import hermes_yaml as yaml

from gateway.business.config import load_business_config
from gateway.business.control import register_business_control_handlers
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.control_socket import GatewayControlServer
from hermes_cli import plugins as plugins_mod
from hermes_cli.plugins import PluginManager


PLUGIN = '''
import json, os

def register(ctx):
    def handler(args, **kwargs):
        return json.dumps(dict(pid=os.getpid(), result=ctx.business_request(args.get('operation','submit_job'), args.get('params',{}))))
    ctx.register_tool(name='business_host_probe',toolset='business_host_probe', schema={'name':'business_host_probe','description':'probe','parameters':{'type':'object','properties':{}}},handler=handler)
'''


@pytest.mark.asyncio
@pytest.mark.platforms('posix')
@pytest.mark.parametrize('isolation',['in_process','host'])
async def test_real_plugin_context_serializes_jobs_to_gateway(tmp_path, monkeypatch, isolation):
    from tools.registry import registry
    home = tmp_path / '.hermes'
    plugin_dir = home / 'plugins' / 'business_probe'
    plugin_dir.mkdir(parents=True)
    home.chmod(0o700)
    (plugin_dir/'plugin.yaml').write_text(yaml.safe_dump({'name':'business_probe','version':'1.0'}))
    (plugin_dir/'__init__.py').write_text(PLUGIN)
    (home/'config.yaml').write_text(yaml.safe_dump({'plugins':{'enabled':['business_probe'],'isolation':isolation}}))
    monkeypatch.setenv('HERMES_HOME',str(home))
    monkeypatch.setenv('HOME',str(tmp_path))
    bundled = tmp_path / 'bundled'; bundled.mkdir()
    monkeypatch.setattr(plugins_mod,'get_bundled_plugins_dir',lambda:bundled)
    store = BusinessStore(home)
    service = BusinessService(store,config_resolver=load_business_config({'mode':'shadow'},supervised_latch=False))
    service.runtime_profile = 'default'
    server = GatewayControlServer(home)
    register_business_control_handlers(server,service,loop=asyncio.get_running_loop())
    assert await server.start()
    manager = PluginManager(scope_key=str(home))
    try:
        await asyncio.to_thread(manager.discover_and_load)
        assert manager._plugins['business_probe'].error is None
        args = {'params':{'contact':'27800000001@s.whatsapp.net','intent':'outreach','request_id':'host-one'}}
        result = json.loads(await asyncio.to_thread(registry.dispatch,'business_host_probe',args,scope=manager.scope_key))
        assert (result['pid'] != os.getpid()) is (isolation == 'host')
        assert result['result']['state'] == 'held'
        replay = json.loads(await asyncio.to_thread(registry.dispatch,'business_host_probe',args,scope=manager.scope_key))
        assert replay['result']['job_id'] == result['result']['job_id']
        with store._connect() as db:
            row = db.execute('SELECT plugin_id,runtime_profile FROM business_plugin_jobs').fetchone()
            assert tuple(row) == ('plugin:business_probe','default')
    finally:
        manager.unload()
        if isolation == 'host':
            manager._plugin_host().shutdown()
        await server.stop()
