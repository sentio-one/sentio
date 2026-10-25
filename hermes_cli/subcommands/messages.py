"""Parser for the native supervised message operation table."""
from __future__ import annotations

import argparse
from typing import Callable


def build_messages_parser(subparsers, *, cmd_messages: Callable | None = None) -> None:
    parser = subparsers.add_parser('messages', help='Review supervised messages and delivery evidence')
    parser.set_defaults(func=cmd_messages or messages_command)
    commands = parser.add_subparsers(dest='messages_command',metavar='<subcommand>')
    descriptions = {
        'queue':'List durable outbox records', 'show':'Show an outbox item and exact plan',
        'edit':'Replace a revision and run checks and advisory review',
        'approve':'Approve an exact revision and rendered digest', 'reject':'Reject a revision',
        'tasks':'List operator attention tasks','resolve-task':'Resolve an attention task',
        'pause':'Pause account dispatch','resume':'Resume account dispatch','digest':'Create an immutable review snapshot',
        'recard':'Show a revision and its current rendered plan','reconcile':'Reconcile uncertain attempts without retrying',
        'status':'Show account mode, bridge and pause state','readiness':'Check workflow readiness',
        'mode':'Set account mode under readiness guards','propose-lessons':'Propose lessons for confirmation',
        'confirm-lesson':'Confirm a proposed lesson','revoke-lesson':'Revoke a lesson',
        'credential-grant':'Grant scoped protected credential delivery','inventory':'Inspect a legacy inventory',
        'import-legacy':'Import legacy work while paused','prepare-cutover':'Prepare paused ownership transfer',
        'apply-cutover':'Apply a prepared paused ownership transfer','rollback-cutover':'Roll back ownership transfer',
    }
    for name, help_text in descriptions.items():
        command = commands.add_parser(name,help=help_text)
        command.add_argument('--json', action='store_true',help='Emit machine-readable results')
        command.add_argument('--runtime-profile',help='Served profile that owns these records')
        command.add_argument('--transport-profile',help='Sending adapter profile')
        command.add_argument('--account-id',help='Sending account identity')
        command.add_argument('--contact-id',help='Canonical contact identity')
        _arguments(command,name)


def _arguments(parser, name):
    positional = {
        'show':('outbox_id',),'recard':('revision_id',),'edit':('revision_id','text'),
        'approve':('revision_id',),'reject':('revision_id',),'resolve-task':('task_id',),
        'mode':('mode',),'confirm-lesson':('lesson_id',),'revoke-lesson':('lesson_id',),
        'credential-grant':('vault_item_id',),'inventory':('path',),'import-legacy':('path',),
        'prepare-cutover':('path',),'apply-cutover':('manifest_id',),'rollback-cutover':('manifest_id',),
    }
    for argument in positional.get(name,()):
        parser.add_argument(argument)
    if name == 'approve':
        parser.add_argument('--plan-digest',required=True,help='Digest displayed by show/recard')
        parser.add_argument('--override-reason')
    if name in {'reject','resolve-task','pause','revoke-lesson'}:
        parser.add_argument('--reason',required=name in {'resolve-task','revoke-lesson'})
    if name == 'propose-lessons':
        parser.add_argument('--policy-id',default='default')
        parser.add_argument('--text',dest='texts',action='append',required=True)
        parser.add_argument('--evidence-revision-id',dest='evidence_revision_ids',action='append',default=[])
    if name == 'credential-grant':
        parser.add_argument('--expires-at-ms',type=int,required=True)
    if name == 'prepare-cutover':
        parser.add_argument('--legacy-stopped', action='store_true', help='Attest that every legacy writer is stopped')
    if name in {'import-legacy','prepare-cutover','apply-cutover'}:
        parser.add_argument('--source-digest',required=True)


def messages_command(args: argparse.Namespace) -> int:
    from hermes_cli.business_messages import run_messages_command
    return run_messages_command(args)
