# core/workflow_state.py
"""Tracks the active workflow per workspace (remembers last selection in each)."""

import threading

from core.config import settings

_workflow_map: dict[str, str] = {}
_current_workspace: str = "main"
_lock: threading.RLock = threading.RLock()


def set_active_workflow(name: str) -> None:
    """Set the active workflow for the current workspace."""
    with _lock:
        _workflow_map[_current_workspace] = name


def get_active_workflow() -> str:
    """Get the active workflow for the current workspace."""
    with _lock:
        default = settings.default_workflow or "default"
        return _workflow_map.get(_current_workspace, default)


def switch_workspace(workspace: str) -> None:
    """Update current workspace without losing saved preferences."""
    global _current_workspace
    with _lock:
        _current_workspace = workspace
