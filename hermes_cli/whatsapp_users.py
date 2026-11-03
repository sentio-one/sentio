"""Manage people routed through the default profile's shared WhatsApp bot."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass

from hermes_cli.config import (
    atomic_config_replace, atomic_config_write, load_env, read_user_config_raw,
    remove_env_value, require_env_writable, save_env_value,
)

ALLOWLIST_ENV = 'WHATSAPP_ALLOWED_USERS'


class WhatsAppUsersError(ValueError):
    """An operator-correctable shared-number configuration error."""


@dataclass(frozen=True)
class WhatsAppUser:
    number: str
    profile: str
    status: str  # owner, live, parked, missing


def _config_path():
    from hermes_cli.profiles import get_profile_dir
    return get_profile_dir('default') / 'config.yaml'


def _require_default_profile():
    from hermes_cli.profiles import get_active_profile_name
    if get_active_profile_name() != 'default':
        raise WhatsAppUsersError('Run with -p default; the default profile owns the shared number.')


@contextmanager
def _locked():
    from hermes_cli.profile_routes import routes_lock
    _require_default_profile()
    with routes_lock(_config_path().parent):
        yield


def _normalize_number(raw):
    from gateway.whatsapp_identity import normalize_whatsapp_identifier
    text = str(raw or '').strip()
    if text.lower().endswith(('@g.us', '@broadcast', '@newsletter', '@lid')):
        raise WhatsAppUsersError('Supply a person phone number with country code, not a group or LID.')
    number = normalize_whatsapp_identifier(text)
    if not number.isascii() or not number.isdigit() or not 7 <= len(number) <= 15:
        raise WhatsAppUsersError(f'{raw!r} is not a phone number with country code.')
    return number


def _same_person(left, right):
    from gateway.whatsapp_identity import expand_whatsapp_aliases
    return bool(expand_whatsapp_aliases(str(left)) & expand_whatsapp_aliases(str(right)))


def _read():
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.profile_routes import effective_route_settings
    data = read_user_config_raw(_config_path())
    try:
        gateway = effective_route_settings(load_user_config_effective(_config_path(), fail_closed=True))
    except ValueError as error:
        raise WhatsAppUsersError(str(error)) from error
    return data, gateway


def _candidate_routes(gateway):
    rows = []
    for row in gateway.get('profile_routes', []):
        if not isinstance(row, dict) or row.get('platform') != 'whatsapp':
            continue
        if str(row.get('bot_profile') or '').strip() not in ('', 'default'):
            continue
        chat = str(row.get('chat_id') or '').lower()
        if chat.endswith(('@g.us', '@broadcast', '@newsletter')):
            continue
        rows.append(row)
    return rows


def _managed(row):
    try:
        _normalize_number(row.get('chat_id'))
    except WhatsAppUsersError:
        return False
    return bool(row.get('enabled', True)) and not any(
        key in row for key in ('user_id', 'guild_id', 'thread_id')
    ) and bool(row.get('profile'))


def _overlaps(row, number):
    # A selector outside the managed chat-only shape can outrank the generated route.
    # Conservatively refuse unresolved overlaps, including broad default-bot routes.
    selectors = [row[key] for key in ('chat_id', 'user_id') if row.get(key)]
    return not selectors or any(_same_person(value, number) for value in selectors)


def _matches(gateway, *, number=None, profile=None):
    rows = [r for r in _candidate_routes(gateway)
            if (number and _overlaps(r, number)) or (profile and r.get('profile') == profile)]
    if len(rows) > 1 or any(not _managed(r) for r in rows):
        raise WhatsAppUsersError('Ambiguous, disabled or restricted WhatsApp routes; resolve them in config first.')
    return rows


def _status(profile):
    from hermes_cli.profiles import get_profile_dir, profile_exists, profile_is_parked
    if not profile_exists(profile):
        return 'missing'
    return 'parked' if profile_is_parked(get_profile_dir(profile)) else 'live'


def _allowed():
    return [x.strip() for x in (load_env().get(ALLOWLIST_ENV) or '').split(',') if x.strip()]


def _allow_number(number):
    allowed = _allowed()
    if not any(_same_person(number, item) for item in allowed):
        save_env_value(ALLOWLIST_ENV, ','.join([*allowed, number]))


def _disallow_number(number):
    save_env_value(ALLOWLIST_ENV, ','.join(x for x in _allowed() if not _same_person(number, x)))


def _write_routes(data, gateway, routes):
    from hermes_cli.profile_routes import require_routes_writable, set_profile_routes
    try:
        require_routes_writable()
    except ValueError as error:
        raise WhatsAppUsersError(str(error)) from error
    set_profile_routes(data, routes)
    atomic_config_write(_config_path(), data)


@contextmanager
def _transaction(data, created_profile=None):
    original = deepcopy(data)
    allowlist = load_env().get(ALLOWLIST_ENV)
    require_env_writable(ALLOWLIST_ENV, 'set')
    try:
        yield
    except BaseException as error:
        actions = [lambda: atomic_config_replace(_config_path(), original),
                   lambda: remove_env_value(ALLOWLIST_ENV) if allowlist is None
                   else save_env_value(ALLOWLIST_ENV, allowlist)]
        if created_profile:
            from hermes_cli.profiles import delete_profile
            actions.append(lambda: delete_profile(created_profile, yes=True))
        for action in actions:
            try:
                action()
            except BaseException as cleanup_error:  # health: allow BLE001 -- attempt every rollback writer and keep the initiating failure primary
                error.add_note(f'Rollback failed: {cleanup_error}')
        raise


def add_user(name: str, number: str) -> WhatsAppUser:
    """Create a dedicated profile and reconcile its persisted WhatsApp admission."""
    from gateway.whatsapp_owners import is_owner, parse_owners
    from hermes_cli.profiles import create_profile, get_profile_dir, normalize_profile_name, profile_exists, validate_profile_name
    with _locked():
        try:
            canon = normalize_profile_name(name)
            validate_profile_name(canon)
        except ValueError as error:
            raise WhatsAppUsersError(str(error)) from error
        if canon == 'default':
            raise WhatsAppUsersError('The default profile belongs to owners; choose a dedicated profile.')
        digits = _normalize_number(number)
        data, gateway = _read()
        owners = parse_owners(gateway.get('owners'))
        if not owners:
            raise WhatsAppUsersError('Set gateway.owners before adding people.')
        if is_owner(owners, digits):
            raise WhatsAppUsersError('This number is an owner; owners use the default profile.')
        rows = _matches(gateway, number=digits)
        if rows:
            if rows[0].get('profile') != canon:
                raise WhatsAppUsersError('This number is already routed to another profile.')
            if not any(_same_person(digits, item) for item in _allowed()):
                with _transaction(data):
                    _allow_number(digits)
            return WhatsAppUser(digits, canon, _status(canon))
        if _matches(gateway, profile=canon):
            raise WhatsAppUsersError('This profile is already mapped to another number.')
        created = not get_profile_dir(canon).exists()
        needs_profile = not profile_exists(canon)
        with _transaction(data, canon if created else None):
            if needs_profile:
                create_profile(canon, no_alias=True)
            routes = [*gateway.get('profile_routes', []), dict(
                name=f'whatsapp-{canon}', platform='whatsapp', profile=canon, chat_id=digits)]
            _write_routes(data, gateway, routes)
            _allow_number(digits)
        return WhatsAppUser(digits, canon, _status(canon))


def list_users() -> list[WhatsAppUser]:
    from gateway.whatsapp_owners import parse_owners
    with _locked():
        _, gateway = _read()
        users = [WhatsAppUser(_normalize_number(x), 'default', 'owner')
                 for x in parse_owners(gateway.get('owners'))]
        for row in _candidate_routes(gateway):
            if not _managed(row):
                raise WhatsAppUsersError('Restricted or malformed WhatsApp route; resolve it in config first.')
            _matches(gateway, profile=row.get('profile'))
            _matches(gateway, number=row['chat_id'])
            users.append(WhatsAppUser(_normalize_number(row['chat_id']), row['profile'], _status(row['profile'])))
        return users


def remove_user(target: str, *, delete_profile: bool = False) -> WhatsAppUser:
    from hermes_cli.profiles import delete_profile as delete, normalize_profile_name
    with _locked():
        data, gateway = _read()
        try:
            number_target = _normalize_number(target)
        except WhatsAppUsersError:
            rows = _matches(gateway, profile=normalize_profile_name(target))
        else:
            rows = _matches(gateway, number=number_target)
        if not rows:
            raise WhatsAppUsersError(f'No WhatsApp route for {target!r}.')
        row = rows[0]
        number, profile = _normalize_number(row['chat_id']), row['profile']
        with _transaction(data):
            _write_routes(data, gateway, [r for r in gateway.get('profile_routes', []) if r is not row])
            _disallow_number(number)
        # A destructive delete cannot be rolled back by restoring the route to a reusable name.
        if delete_profile:
            delete(profile, yes=True)
        return WhatsAppUser(number, profile, _status(profile))
