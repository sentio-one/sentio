"""Report a repeating condition at WARNING the first time per process, then at DEBUG."""

from __future__ import annotations

import threading
from typing import Hashable

_seen: set[Hashable] = set()
_lock = threading.Lock()


def first_report(key: Hashable) -> bool:
    """True the first time ``key`` is seen in this process."""
    with _lock:
        if key in _seen:
            return False
        _seen.add(key)
        return True
