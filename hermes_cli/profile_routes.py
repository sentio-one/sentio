"""Static route ownership, shared-user configuration and profile lifecycle writes."""
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
import json
from pathlib import Path
import threading

_THREAD_LOCK = threading.RLock()
_HELD_LOCKS = threading.local()


def profile_routes_locked(operation):
    """Keep shared-user writes serialized through the entire delete/rename operation."""
    @wraps(operation)
    def locked(*args, **kwargs):
        from hermes_cli.profiles import get_profile_dir
        with routes_lock(get_profile_dir('default')):
            return operation(*args, **kwargs)
    return locked


@contextmanager
def routes_lock(home: Path):
    """Serialize shared-user and lifecycle writes, including nested remove-user deletion."""
    from hermes_cli.active_sessions import _FileLock
    key = str(home.resolve())
    with _THREAD_LOCK:
        held = getattr(_HELD_LOCKS, 'homes', set())
        if key in held:
            yield
            return
        with _FileLock(home / '.whatsapp_users.lock'):
            _HELD_LOCKS.homes = held | {key}
            try:
                yield
            finally:
                _HELD_LOCKS.homes = held


def effective_route_settings(data: dict) -> dict:
    """Use the gateway's top-level-over-nested, null-fallback precedence without defaults."""
    from gateway.config_loader import bridge_toplevel_keys
    nested = data.get('gateway', {})
    if not isinstance(nested, dict):
        raise ValueError('gateway must be a mapping.')
    for key in ('owners', 'profile_routes'):
        value = data.get(key)
        if value is None:
            value = nested.get(key)
        if value is not None and not isinstance(value, list):
            raise ValueError(f'{key} must be a list.')
    settings = {}
    bridge_toplevel_keys(data, nested, settings)
    return settings


def set_profile_routes(data: dict, routes: list) -> None:
    """Preserve the authored location the gateway actually reads."""
    if data.get('profile_routes') is not None:
        data['profile_routes'] = routes
    else:
        data.setdefault('gateway', {})['profile_routes'] = routes


def require_routes_writable() -> None:
    from hermes_cli.managed_scope import is_key_managed
    if any(is_key_managed(key) for key in ('profile_routes', 'gateway.profile_routes')):
        raise ValueError('profile_routes is managed by your administrator; update that policy first.')


def _reload_live_routes(home: Path) -> None:
    from gateway.control_socket import query_gateway_control
    from hermes_cli.gateway_multiplex_served import live_default_gateway_pid
    if live_default_gateway_pid() is None:
        return
    answer = query_gateway_control(home, 'reload-profile-routes')
    if not isinstance(answer, dict) or answer.get('ok') is not True:
        raise RuntimeError('The live gateway could not reload profile routes. Restart it before retrying.')


def _rewrite_rows(rows, old: str, new: str | None):
    if not isinstance(rows, list):
        raise ValueError('profile_routes must be a list.')
    result = []
    for row in rows:
        keys = []
        if isinstance(row, dict):
            if str(row.get('profile') or '').strip().lower() == old:
                keys.append('profile')
            if str(row.get('bot_profile') or '').strip() == old:
                keys.append('bot_profile')
        if keys and new is None:
            continue
        updated = dict(row) if keys else row
        for key in keys:
            updated[key] = new
        result.append(updated)
    return result


@contextmanager
def changing_profile_routes(old: str, new: str | None = None):
    """Settle persisted AND live routes before a profile name can change ownership.

    A failed filesystem rename restores the original mapping. Deletion keeps revocation
    even if directory cleanup subsequently fails, so a partial delete never revives access.
    """
    from hermes_cli.config import atomic_config_replace, atomic_config_write, read_user_config_raw
    from hermes_cli.profiles import get_profile_dir
    from gateway.profile_route_lifecycle import require_profile_routes_detached
    home = get_profile_dir('default')
    path = home / 'config.yaml'
    with routes_lock(home):
        original = read_user_config_raw(path)
        data = deepcopy(original)
        containers = [data]
        if isinstance(data.get('gateway'), dict):
            containers.append(data['gateway'])
        # Legacy gateway.json is a base layer. Materialize a YAML override only when
        # its routes are authoritative and actually reference the changing name.
        if effective_route_settings(data).get('profile_routes') is None:
            legacy_path = home / 'gateway.json'
            legacy = json.loads(legacy_path.read_text(encoding='utf-8-sig')) if legacy_path.exists() else {}
            rows = legacy.get('profile_routes') or []
            rewritten = _rewrite_rows(rows, old, new)
            if rewritten != rows:
                set_profile_routes(data, rewritten)
        for container in containers:
            if container.get('profile_routes') is not None:
                container['profile_routes'] = _rewrite_rows(container['profile_routes'], old, new)
        if data == original:
            require_profile_routes_detached(home, old)
            # A prior remove-user may already have changed the file while the gateway
            # still holds its startup routes. Name reuse must settle that cache too.
            _reload_live_routes(home)
            yield
            return
        require_routes_writable()
        try:
            atomic_config_write(path, data)
            require_profile_routes_detached(home, old)
            _reload_live_routes(home)
            yield
        except BaseException as error:
            try:
                atomic_config_replace(path, original)
                _reload_live_routes(home)
            except BaseException as cleanup_error:  # health: allow BLE001 -- retain the initiating failure when route rollback also fails
                error.add_note(f'Profile route rollback failed: {cleanup_error}')
            raise


def revoke_profile_routes(name: str) -> None:
    with changing_profile_routes(name):
        pass


def rename_profile_directory(old: Path, new: Path) -> None:
    with changing_profile_routes(old.name, new.name):
        old.rename(new)
