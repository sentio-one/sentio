"""PluginContext refuses privileged operations and reports native gateway outages."""
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


def test_actual_context_refuses_approval_and_offline_is_an_error(tmp_path):
    context = PluginContext(PluginManifest(name='sales',version='1.0'),PluginManager(scope_key=str(tmp_path)))
    assert context.business_request('approve',{'revision_id':'revision'})['error'] == 'permission_denied'
    assert context.business_request('set_mode',{'mode':'supervised'})['error'] == 'permission_denied'
    assert context.business_request('submit_job',{'contact':'person','intent':'outreach','request_id':'one'})['error'] == 'offline'
