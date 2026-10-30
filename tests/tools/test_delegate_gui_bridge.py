"""A delegated child that inherits ``desktop_ui`` must reach the parent's visible preview pane.

Without the parent's renderer-bridge callbacks the child is offered ``drive_preview`` but every
call answers "only available in the Hermes desktop app", pushing the model to the invisible
``browser_*`` tools.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools.delegate_tool import _build_child_agent


def _desktop_parent():
    parent = MagicMock()
    parent.base_url = "https://openrouter.ai/api/v1"
    parent.api_key = "***"
    parent.provider = "openrouter"
    parent.api_mode = "chat_completions"
    parent.model = "anthropic/claude-sonnet-4"
    parent.platform = "desktop"
    parent.providers_allowed = parent.providers_ignored = parent.providers_order = parent.provider_sort = None
    parent._session_db = None
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent.enabled_toolsets = ["terminal", "file", "desktop_ui"]
    parent.disabled_toolsets = []
    parent.drive_preview_callback = lambda payload: "{}"
    parent.read_preview_callback = lambda **kw: "{}"
    parent.read_terminal_callback = lambda **kw: "{}"
    return parent


def test_child_inheriting_desktop_ui_gets_the_parents_preview_bridge():
    parent = _desktop_parent()

    with patch("run_agent.AIAgent") as agent_cls:
        agent_cls.return_value = MagicMock()
        _build_child_agent(
            task_index=0, goal="log in", context=None, toolsets=None, model=None,
            max_iterations=10, parent_agent=parent, task_count=1, role="leaf",
        )

    kwargs = agent_cls.call_args.kwargs
    assert "desktop_ui" in kwargs["enabled_toolsets"]
    assert kwargs["drive_preview_callback"] is parent.drive_preview_callback
    assert kwargs["read_preview_callback"] is parent.read_preview_callback
