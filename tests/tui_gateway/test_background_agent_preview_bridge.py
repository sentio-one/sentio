"""A /background agent spawned from a desktop session must be able to drive the visible preview pane.

The parent desktop agent carries ``desktop_ui`` plus the renderer-bridge callbacks from
``_agent_cbs``. The side agent keeps the toolset (copied from the parent) but is built from
``_background_agent_kwargs`` alone, so ``drive_preview`` / ``desktop_preview read`` are offered
yet have no callback and answer "only available in the Hermes desktop app" -- the model then
falls back to the invisible ``browser_*`` tools.
"""

from types import SimpleNamespace

import pytest

from tui_gateway import server


@pytest.fixture
def desktop_parent():
    return SimpleNamespace(
        enabled_toolsets=["terminal", "file", "browser", "desktop_ui"],
        disabled_toolsets=None,
        model="m",
        provider="p",
        _session_db=None,
        _fallback_chain=[],
    )


def test_background_agent_keeps_gui_toolset(desktop_parent):
    kwargs = server._background_agent_kwargs(desktop_parent, "bg_1")

    assert "desktop_ui" in kwargs["enabled_toolsets"]


def test_background_agent_can_reach_the_visible_preview_pane(desktop_parent):
    kwargs = server._background_agent_kwargs(desktop_parent, "bg_1", "parent-sid")

    if "desktop_ui" in kwargs["enabled_toolsets"]:
        assert callable(kwargs.get("drive_preview_callback")), (
            "desktop_ui tools are offered to the background agent but drive_preview has no "
            "renderer bridge"
        )
        assert callable(kwargs.get("read_preview_callback"))
