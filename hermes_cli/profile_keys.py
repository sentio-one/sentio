"""``hermes profile share-keys`` and the key-sharing lines of ``profile create/show`` (private fork, D15).

Only records the admin's choice in the launch home (``gateway/profile_key_sharing.py``); no key is read or printed here,
so it works from the main agent's terminal, where provider keys are deliberately not visible.
"""

from __future__ import annotations

from hermes_cli.profile_cmd import _die

SHARED = "the company's model keys (shared by the admin, copied nowhere)"


def keys_line(name: str) -> str:
    from gateway.profile_key_sharing import sharing_profiles
    from hermes_cli.profiles import normalize_profile_name

    return f"Keys:    {SHARED if normalize_profile_name(name) in sharing_profiles() else 'its own (.env)'}"


def profile_share_keys(args) -> None:
    from gateway.profile_key_sharing import set_sharing
    from hermes_cli.profiles import profile_exists

    name, enabled = args.profile_name, not args.off
    if not profile_exists(name):
        _die(f"Error: Profile '{name}' does not exist.")
    try:
        set_sharing(name, enabled)
    except ValueError as exc:
        _die(f"Error: {exc}")
    print(f"Profile '{name}' now uses {SHARED}." if enabled
          else f"Profile '{name}' no longer uses the company's keys. Give it its own: {name} setup")
