"""``hermes whatsapp add-user | list-users | remove-user`` (logic: hermes_cli/whatsapp_users.py)."""

from __future__ import annotations

import sys

from hermes_cli import whatsapp_users as wu


def _add(args) -> None:
    user = wu.add_user(args.name, args.number)
    print(f"Added {user.profile} for +{user.number}.")
    print("  - The profile has no model key yet: give it one before they message (shared company model keys).")
    print("  - Routes load when the gateway starts: run `hermes gateway restart` to apply.")


def _list(args) -> None:
    users = wu.list_users()
    if not users:
        print("No people on the shared WhatsApp number yet.")
    for user in users:
        print(f"+{user.number:<16} {user.profile:<24} {user.status}")


def _remove(args) -> None:
    user = wu.remove_user(args.target, delete_profile=args.delete_profile)
    kept = "deleted" if args.delete_profile else "kept"
    print(f"Removed +{user.number} ({user.profile}); profile {kept}. Run `hermes gateway restart` to apply.")


_ACTIONS = {"add-user": _add, "list-users": _list, "remove-user": _remove}


def run(args) -> int:
    try:
        _ACTIONS[args.whatsapp_action](args)
    except wu.WhatsAppUsersError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
