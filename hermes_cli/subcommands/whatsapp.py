"""``hermes whatsapp`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def build_whatsapp_parser(subparsers, *, cmd_whatsapp: Callable) -> None:
    """Attach the ``whatsapp`` subcommand to ``subparsers``."""
    whatsapp_parser = subparsers.add_parser(
        "whatsapp", help="Set up WhatsApp integration",
        description="Configure WhatsApp and pair via QR code")
    whatsapp_parser.set_defaults(func=cmd_whatsapp, whatsapp_action=None)
    actions = whatsapp_parser.add_subparsers(dest="whatsapp_action", metavar="ACTION")
    add = actions.add_parser(
        "add-user", help="Give a person their own profile on the shared WhatsApp number",
        description="Create the profile, route the number to it and admit it (default profile only).")
    add.add_argument("name", help="Profile name for the person, e.g. sentio-sales")
    add.add_argument("number", help="Their WhatsApp number with country code, e.g. +27820000002")
    actions.add_parser("list-users", help="List owners and the people routed on the shared number")
    remove = actions.add_parser(
        "remove-user", help="Stop routing a person (keeps their profile)",
        description="Remove the route and the allowlist entry; the profile and its data stay unless --delete-profile.")
    remove.add_argument("target", help="Profile name or WhatsApp number")
    remove.add_argument("--delete-profile", action="store_true", help="Also delete the profile and its data")


def build_whatsapp_cloud_parser(subparsers, *, cmd_whatsapp_cloud: Callable) -> None:
    """Attach the ``whatsapp-cloud`` subcommand (official Meta Cloud API)."""
    whatsapp_cloud_parser = subparsers.add_parser(
        "whatsapp-cloud", help="Set up WhatsApp Business Cloud API integration",
        description="Configure the official Meta WhatsApp Business Cloud API "
            "adapter (Business account required, public webhook URL "
            "required). Distinct from `hermes whatsapp` which sets up "
            "the Baileys bridge for personal accounts.")
    whatsapp_cloud_parser.set_defaults(func=cmd_whatsapp_cloud)
