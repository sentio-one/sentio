"""Authenticated business operations on the owning gateway event loop."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import asdict, is_dataclass
import logging
import time
from typing import Any

from gateway.business.models import BusinessConflict, BusinessDenied, BusinessProtocolError, Principal, Scope
from gateway.business.permissions import OPERATOR_OPERATIONS, PERMITTED_PLUGIN_OPERATIONS
from gateway.business.sandbox_boundary import socket_operation_refused

logger = logging.getLogger(__name__)


def wire_value(value):
    if is_dataclass(value):
        return wire_value(asdict(value))
    if isinstance(value, dict):
        return {k: wire_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [wire_value(v) for v in value]
    return value


def profile_home(service, profile):
    resolver = getattr(service, '_config_resolver', None)
    if callable(getattr(resolver, 'profile_home', None)):
        home = resolver.profile_home(profile)
        if home is not None:
            return home
        raise BusinessDenied('profile_not_served')
    runner = getattr(service, 'runner', None)
    if runner is not None:
        homes = getattr(runner, '_served_profile_homes', {}) or {}
        if profile in homes:
            return homes[profile]
    if profile == getattr(service, 'runtime_profile', 'default'):
        return service.store.db_path.parent
    raise BusinessDenied('profile_not_served')


async def execute_business_request(service, *, operation, params, principal):
    """Run one operation with immutable authenticated identity and profile scope."""
    if principal is None:
        return {'ok': False, 'error': 'permission_denied'}
    try:
        home = profile_home(service, principal.runtime_profile)
        runtime_scope = getattr(service, 'runtime_scope', None)
        if runtime_scope is None and getattr(service, 'runner', None) is not None:
            from gateway.run import _profile_runtime_scope
            runtime_scope = lambda profile: _profile_runtime_scope(home)
        with runtime_scope(principal.runtime_profile) if callable(runtime_scope) else nullcontext():
            if operation == 'ping':
                return {'ok': True, 'pong': True}
            if operation == 'business.consume':
                return _consume(service, params, principal)
            if operation.startswith('plugin.'):
                from gateway.business.jobs import handle_plugin_request
                return wire_value(handle_plugin_request(service, operation[7:], params, principal))
            from gateway.business.operator_operations import execute_operator_operation
            action = operation.removeprefix('operator.').removeprefix('business.')
            result = await execute_operator_operation(service, action, params, principal)
            return {'ok': True, **wire_value(result)}
    except (BusinessProtocolError, BusinessConflict) as exc:
        return {'ok': False, 'error': str(exc)}
    except BusinessDenied:
        return {'ok': False, 'error': 'permission_denied'}
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug('Invalid business request: %s', exc)
        return {'ok': False, 'error': 'invalid_request'}
    except Exception:
        logger.exception('Business control request failed')
        return {'ok': False, 'error': 'internal_error'}


def _consume(service, params, principal):
    from gateway.business.dispatch import consume_authorization
    from gateway.business.dispatch import _default_transport_key
    if principal.kind != 'transport':
        raise BusinessDenied()
    scope = Scope(**params['scope'])
    if scope.runtime_profile != principal.runtime_profile:
        raise BusinessDenied()
    attempt = consume_authorization(
        params['token'], payload_digest=params['payload_digest'], now_ms=int(time.time()*1000),
        store=service.store, secret_key=_default_transport_key(service.store.db_path.parent), request=params, service=service,
    )
    return {'ok': True, 'attempt_id': attempt.id, 'state': attempt.state}


def handle_business_request(service, *, operation, params, principal):
    """Synchronous entry point for socket workers and offline API clients."""
    coro = execute_business_request(service, operation=operation, params=params, principal=principal)
    loop = getattr(service, '_control_loop', None)
    if loop is not None and loop.is_running():
        try:
            on_owner = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_owner = False
        if on_owner:
            coro.close()
            return {'ok': False, 'error': 'use_async_control_on_owner_loop'}
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=120)
        except TimeoutError:
            future.cancel()
            return {'ok': False, 'error': 'operation_timeout'}
    return asyncio.run(coro)


def register_business_control_handlers(server: Any, service: Any, *, runner=None, loop=None) -> None:
    """Register private verbs; socket peer verification supplies the human identity."""
    if loop is None:
        loop = asyncio.get_running_loop()
    service._control_loop = loop
    if runner is not None:
        service.runner = runner

    def handle_request(params, peer_subject):
        op, payload = params.get('operation', ''), params.get('params', {})
        if not isinstance(op, str) or not isinstance(payload, dict):
            return {'ok': False, 'error': 'invalid_request'}
        # Same-user local peers can select a served profile, but cannot select capabilities.
        profile = payload.get('runtime_profile', getattr(service, 'runtime_profile', 'default'))
        plugin = op.startswith('plugin.')
        principal = Principal(
            subject=f"plugin:{payload.get('plugin_id', 'local')}" if plugin else peer_subject,
            runtime_profile=profile,
            operations=PERMITTED_PLUGIN_OPERATIONS if plugin else OPERATOR_OPERATIONS | frozenset({"admin"}),
            kind='plugin' if plugin else 'human',
        )
        if not plugin and socket_operation_refused(service, profile, op):
            return {'ok': False, 'error': 'sandbox_required_for_socket_operation'}
        return handle_business_request(service, operation=op, params=payload, principal=principal)

    def handle_transport(params, peer_subject):
        scope = params.get('scope') or {}
        profile = scope.get('runtime_profile', getattr(service, 'runtime_profile', 'default'))
        principal = Principal(peer_subject, profile, frozenset({'transport'}), 'transport')
        return handle_business_request(service, operation='business.consume', params=params, principal=principal)

    server.private_handlers['business.request'] = handle_request
    server.private_handlers['business.transport'] = handle_transport
