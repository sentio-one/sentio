"""The desktop platform hint must steer "open your browser" to the visible preview pane.

Only the pane is visible to the user; ``browser_*`` runs a hidden browser. A hint that never says so
leaves the model free to open the hidden one for a login the user has to complete.
"""

from agent.prompt_builder import PLATFORM_HINTS


def test_desktop_hint_routes_user_visible_browsing_to_the_preview_pane():
    hint = PLATFORM_HINTS["desktop"]

    assert "desktop_preview" in hint and "drive_preview" in hint
    assert "hidden" in hint and "browser_*" in hint
