"""An old config backup the current user cannot read must not stop new backups (private fork, found on the live gateway).

A command run as root in a container leaves a root-owned, mode 0600/0640 backup behind. The gateway user then failed to read it
for the identical-bytes check, abandoned the whole backup, and logged "Could not back up config.yaml (good): Permission denied"
on every start, with no backup ever written again.
"""
from pathlib import Path

from hermes_cli.config_backups import backup_config, list_config_backups


def _stamps(monkeypatch):
    stamps = iter(f"2026010100000{i}" for i in range(10))
    monkeypatch.setattr("hermes_cli.config_backups.time.strftime", lambda _fmt: next(stamps))


def _unreadable(monkeypatch, victim: Path):
    real = Path.read_bytes

    def read_bytes(self):
        if self == victim:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def test_an_unreadable_newest_backup_does_not_block_a_new_one(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model: a\n")
    _stamps(monkeypatch)
    old = backup_config(cfg, "good")
    assert old is not None
    _unreadable(monkeypatch, old)

    new = backup_config(cfg, "good")

    assert new is not None and new != old and new.read_text() == "model: a\n"
    assert list_config_backups(cfg, "good")[0] == new


def test_an_unreadable_backup_is_still_rotated_out(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model: a\n")
    _stamps(monkeypatch)
    old = backup_config(cfg, "good", keep=1)
    _unreadable(monkeypatch, old)
    cfg.write_text("model: b\n")

    new = backup_config(cfg, "good", keep=1)

    assert list_config_backups(cfg, "good") == [new] and not old.exists()


def test_an_identical_readable_backup_is_still_skipped(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model: a\n")
    _stamps(monkeypatch)
    assert backup_config(cfg, "good") is not None
    assert backup_config(cfg, "good") is None
