"""Default Sentio identity and safe upgrades of untouched Hermes profiles."""
from pathlib import Path

import pytest

from hermes_cli.config import _ensure_default_soul_md
from hermes_cli.default_soul import DEFAULT_SOUL_MD, is_legacy_template_soul


def test_default_identity_names_sentio():
    assert DEFAULT_SOUL_MD.startswith(
        "You are Sentio, an AI agent powered by Sentio One, running in the Sentio One app. "
    )
    assert "Nous Research" not in DEFAULT_SOUL_MD


@pytest.mark.parametrize("ascii_dashes", [False, True])
def test_untouched_hermes_default_upgrades(tmp_path, ascii_dashes):
    from hermes_cli.default_soul import _HERMES_DEFAULT_SOUL_MD

    old = _HERMES_DEFAULT_SOUL_MD
    if ascii_dashes:
        old = old.replace("\u2014", "--")
    assert is_legacy_template_soul(old)
    (tmp_path / "SOUL.md").write_text(old, encoding="utf-8")
    _ensure_default_soul_md(tmp_path)
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == DEFAULT_SOUL_MD


def test_customised_hermes_soul_is_preserved(tmp_path):
    from hermes_cli.default_soul import _HERMES_DEFAULT_SOUL_MD

    custom = _HERMES_DEFAULT_SOUL_MD + "\nAlways answer in French."
    assert not is_legacy_template_soul(custom)
    (tmp_path / "SOUL.md").write_text(custom, encoding="utf-8")
    _ensure_default_soul_md(tmp_path)
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == custom


def test_new_profile_has_sentio_identity(tmp_path, monkeypatch):
    from hermes_cli.profiles import create_profile

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    profile = create_profile("sentio-sales", no_alias=True)
    assert (profile / "SOUL.md").read_text(encoding="utf-8") == DEFAULT_SOUL_MD
