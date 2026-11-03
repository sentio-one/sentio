"""Shared WhatsApp CLI parsing, lifecycle output, and real main dispatch."""
import argparse
from pathlib import Path

import pytest

from hermes_cli.config import atomic_config_write
from hermes_cli.subcommands.whatsapp import build_whatsapp_parser


def _parse(*argv):
    root = argparse.ArgumentParser()
    build_whatsapp_parser(root.add_subparsers(dest="command"), cmd_whatsapp=lambda args: None)
    return root.parse_args(["whatsapp", *argv])


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("WHATSAPP_ALLOWED_USERS", raising=False)
    atomic_config_write(home / "config.yaml", {"gateway": {"owners": ["+27820000001"]}})
    return home


def test_parser_reads_the_three_actions():
    add = _parse("add-user", "sentio-sales", "+27820000002")
    assert (add.whatsapp_action, add.name, add.number) == ("add-user", "sentio-sales", "+27820000002")
    assert _parse("list-users").whatsapp_action == "list-users"
    rm = _parse("remove-user", "sentio-sales", "--delete-profile")
    assert (rm.whatsapp_action, rm.target, rm.delete_profile) == ("remove-user", "sentio-sales", True)
    assert _parse().whatsapp_action is None


def test_add_list_remove_state_and_notices(home, capsys):
    from hermes_cli.whatsapp_users_cli import run
    assert run(_parse("add-user", "sentio-sales", "27820000002")) == 0
    out = capsys.readouterr().out
    assert "sentio-sales" in out and "27820000002" in out
    assert "gateway restart" in out and "model key" in out.lower()
    assert run(_parse("list-users")) == 0
    listing = capsys.readouterr().out
    assert "27820000001" in listing and "owner" in listing and "live" in listing
    assert run(_parse("remove-user", "sentio-sales")) == 0
    assert "profile kept" in capsys.readouterr().out
    assert (home / "profiles/sentio-sales").exists()
    assert run(_parse("add-user", "sentio-sales", "27820000002")) == 0
    assert run(_parse("remove-user", "27820000002", "--delete-profile")) == 0
    assert "profile deleted" in capsys.readouterr().out
    assert not (home / "profiles/sentio-sales").exists()


def test_refusals_exit_non_zero_with_reason(home, capsys):
    from hermes_cli.whatsapp_users_cli import run
    assert run(_parse("add-user", "sentio-sales", "not-a-number")) == 1
    assert "phone number" in capsys.readouterr().err


@pytest.mark.parametrize("argv, code", [(["list-users"], 0), (["add-user", "sales", "bad"], 1)])
def test_main_dispatches_user_actions_without_tty(home, monkeypatch, capsys, argv, code):
    from hermes_cli import main
    import hermes_cli.boot_bootstrap as boot
    monkeypatch.setattr(boot, "maybe_run_boot_bootstrap", lambda *args: None)
    monkeypatch.setattr(main, "_recover_update_debts_on_startup", lambda: None)
    monkeypatch.setattr(main, "_sweep_stale_bytecode_if_checkout_changed", lambda: None)
    monkeypatch.setattr(main.sys, "argv", ["hermes", "whatsapp", *argv])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == code
    captured = capsys.readouterr()
    assert "owner" in captured.out if code == 0 else "phone number" in captured.err


def test_bare_whatsapp_still_enters_setup_wizard(home, monkeypatch, capsys):
    from hermes_cli import main
    from hermes_cli import main_platform_setup
    parser, _ = main._build_cli_parser()
    args = parser.parse_args(["whatsapp"])
    monkeypatch.setattr(main, "_require_tty", lambda name: None)
    monkeypatch.setattr(main_platform_setup, "_whatsapp_choose_mode", lambda *args: None)
    args.func(args)
    assert "WhatsApp Setup" in capsys.readouterr().out


def test_user_commands_ignore_sticky_profile_but_preserve_explicit_guard(home, monkeypatch):
    from hermes_cli import main, profiles
    from hermes_cli.whatsapp_users_cli import run
    profiles.create_profile('selected', no_alias=True)
    profiles.set_active_profile('selected')
    monkeypatch.setattr(main.sys, 'argv', ['hermes', 'whatsapp', 'add-user', 'sales', '27820000002'])
    main._apply_profile_override()
    assert run(_parse('add-user', 'sales', '27820000002')) == 0
    assert (home / 'profiles/sales').is_dir()
    assert not (home / 'profiles/selected/.env').read_text().count('27820000002')
    for action in ('list-users', 'remove-user'):
        argv = [action] if action == 'list-users' else [action, 'sales']
        monkeypatch.setattr(main.sys, 'argv', ['hermes', 'whatsapp', *argv])
        main._apply_profile_override()
        assert run(_parse(*argv)) == 0
    monkeypatch.setattr(main.sys, 'argv', ['hermes', '-p', 'selected', 'whatsapp', 'list-users'])
    main._apply_profile_override()
    assert run(_parse('list-users')) == 1
