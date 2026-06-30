"""Maestro panel builders — extracted from maestro_tab.py."""

from desktop.panels.chat_panel import build_chat_panel
from desktop.panels.detail_panel import build_detail_panel
from desktop.panels.execution_panel import build_execution_panel, group_style
from desktop.panels.top_bar import build_top_bar

__all__ = [
    "build_chat_panel",
    "build_detail_panel",
    "build_execution_panel",
    "build_top_bar",
    "group_style",
]
