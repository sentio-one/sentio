"""Profile-owned business requests shared by in-process and isolated plugin contexts."""
from __future__ import annotations

from gateway.business.models import BusinessDenied, BusinessProtocolError, Principal, Scope
from gateway.business.permissions import PERMITTED_PLUGIN_OPERATIONS, authorize_operation


def handle_plugin_business_request(context_or_principal, scope: Scope | None = None, *, operation: str, params=None):
    from gateway.business.client import request_business
    from hermes_constants import get_hermes_home, profile_name_for_home

    values = dict(params or {})
    if hasattr(context_or_principal, 'manifest'):
        ctx = context_or_principal
        home = ctx._manager.home_path
        runtime = profile_name_for_home(home) or 'default'
        principal = Principal(f'plugin:{ctx.plugin_id}', runtime, PERMITTED_PLUGIN_OPERATIONS, 'plugin')
    else:
        principal = context_or_principal
        home = get_hermes_home()
        if scope:
            values['scope'] = scope.__dict__
    try:
        authorize_operation(principal, scope or Scope(principal.runtime_profile,'','',''), operation)
        requested = values.get('scope') or {}
        if not isinstance(requested, dict):
            return {'ok':False,'error':'invalid_request'}
        if values.get('runtime_profile', principal.runtime_profile) != principal.runtime_profile or requested.get('runtime_profile', principal.runtime_profile) != principal.runtime_profile:
            raise BusinessDenied('permission_denied')
        values['runtime_profile'] = principal.runtime_profile
        values['plugin_id'] = principal.subject.removeprefix('plugin:')
        return request_business(home,operation=f'plugin.{operation}',params=values)
    except BusinessDenied:
        return {'ok':False,'error':'permission_denied'}
    except BusinessProtocolError as exc:
        return {'ok':False,'error':exc.code}
