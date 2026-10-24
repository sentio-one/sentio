"""``hermes messages`` subcommand parser (Task C1)."""

from __future__ import annotations

import argparse
from typing import Callable

from hermes_cli.subcommands._shared import add_json_flag


def build_messages_parser(subparsers, *, cmd_messages: Callable | None = None) -> None:
    """Attach the ``messages`` subcommand to ``subparsers``."""
    messages_parser = subparsers.add_parser(
        "messages",
        help="Supervised message review and outbox operations",
        description="Inspect, review, approve, and reject supervised WhatsApp messages.",
    )
    messages_subparsers = messages_parser.add_subparsers(
        dest="messages_command", metavar="<subcommand>"
    )

    # queue
    queue_parser = messages_subparsers.add_parser(
        "queue",
        help="List held or approved messages awaiting delivery",
    )
    add_json_flag(queue_parser, "Emit machine-readable JSON instead of human-readable text")

    # show
    show_parser = messages_subparsers.add_parser(
        "show",
        help="Show details of a specific outbox message",
    )
    show_parser.add_argument("outbox_id", help="Outbox identifier to inspect")
    add_json_flag(show_parser, "Emit machine-readable JSON instead of human-readable text")

    # approve
    approve_parser = messages_subparsers.add_parser(
        "approve",
        help="Approve a draft revision for physical delivery",
    )
    approve_parser.add_argument("revision_id", help="Revision identifier to approve")

    # reject
    reject_parser = messages_subparsers.add_parser(
        "reject",
        help="Reject a draft revision",
    )
    reject_parser.add_argument("revision_id", help="Revision identifier to reject")
    reject_parser.add_argument("--reason", default="operator_rejected", help="Reason for rejection")

    if cmd_messages is not None:
        messages_parser.set_defaults(func=cmd_messages)


def messages_command(args: argparse.Namespace) -> int:
    """Dispatch ``hermes messages`` subcommands."""
    from hermes_cli.business_messages import run_messages_command
    return run_messages_command(args)
