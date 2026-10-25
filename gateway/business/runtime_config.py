"""Resolve business account policy under the receiving and working profile scopes."""

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

from gateway.business.config import BusinessConfig, BusinessConfigError, load_business_config
from gateway.business.models import Scope
from hermes_constants import get_hermes_home, profile_name_for_home


def raw_business(config):
    platforms = getattr(config, "platforms", {}) or {}
    whatsapp = platforms.get("whatsapp", {})
    extra = whatsapp.get("extra", {}) if isinstance(whatsapp, dict) else getattr(whatsapp, "extra", {})
    return (extra or {}).get("business", {})


def account_latched(store, transport_profile, account_id):
    with store._connect() as conn:
        return conn.execute(
            "SELECT 1 FROM business_account_modes WHERE transport_profile=? AND account_id=? AND mode='supervised'",
            (transport_profile, account_id),
        ).fetchone() is not None


def resolve_stored_config(raw, store, scope):
    profile = scope.transport_profile if isinstance(scope, Scope) else scope
    account = scope.account_id if isinstance(scope, Scope) else profile
    latched = account_latched(store, profile, account)
    try:
        return load_business_config(raw, supervised_latch=latched)
    except BusinessConfigError as exc:
        # A malformed or missing policy never restores a latched account's personal sender.
        try:
            held = load_business_config(raw, supervised_latch=True)
        except BusinessConfigError:
            held = BusinessConfig("supervised", (), (), 0, 86400, 604800, None)
        return replace(held, extra={"configuration_error": exc.code})


class RuntimeConfig:
    def __init__(self, runner, store):
        self.runner, self.store = runner, store
        self.home = Path(getattr(runner, "hermes_home", None) or get_hermes_home())
        self.launch_profile = profile_name_for_home(self.home) or "default"

    def profile_home(self, profile):
        if profile == self.launch_profile:
            return self.home
        return (getattr(self.runner, "_served_profile_homes", {}) or {}).get(profile)

    def runtime_scope(self, scope):
        profile = scope.runtime_profile if isinstance(scope, Scope) else scope
        home = self.profile_home(profile)
        if home is None:
            raise ValueError("business_profile_not_served")
        if not hasattr(self.runner, "_profile_configs") and not hasattr(self.runner, "_served_profile_homes"):
            return nullcontext()
        from gateway.run import _profile_runtime_scope
        return _profile_runtime_scope(home)

    def profiles(self):
        configs = dict(getattr(self.runner, "_profile_configs", {}) or {})
        configs[self.launch_profile] = self.runner.config
        return configs

    def __call__(self, scope):
        profile = scope.transport_profile if isinstance(scope, Scope) else scope
        config = self.profiles().get(profile)
        if config is None:
            return None
        return resolve_stored_config(raw_business(config), self.store, scope)
