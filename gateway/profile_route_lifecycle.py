"""Reload static route relationships during a profile lifecycle operation."""
from pathlib import Path


def load_lifecycle_profile_routes(home: Path):
    from gateway.config_loader import load_legacy_gateway_json
    from gateway.profile_routing import parse_profile_routes
    from gateway.run import _profile_runtime_scope
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.profile_routes import effective_route_settings
    # Lifecycle and control callbacks run outside a turn, possibly under a named profile.
    with _profile_runtime_scope(home):
        data = load_user_config_effective(home / 'config.yaml', fail_closed=True)
        settings = load_legacy_gateway_json(home)
        settings.update(effective_route_settings(data))
        return parse_profile_routes(settings.get('profile_routes'))


def require_profile_routes_detached(home: Path, name: str) -> None:
    if any(r.profile == name or r.bot_profile == name for r in load_lifecycle_profile_routes(home)):
        raise ValueError('Profile routes still reference this name through managed or expanded settings; '
                         'update that policy before deleting or renaming the profile.')


def reload_profile_routes_verb(runner):
    def handler():
        from hermes_constants import get_process_hermes_home
        runner.config.profile_routes = load_lifecycle_profile_routes(Path(get_process_hermes_home()))
        return {'ok': True}
    return handler
