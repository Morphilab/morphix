# mypy: ignore-errors
"""Execution panel — progress bar, stats, subtask list, files created."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import COLORS, StyleFactory

if TYPE_CHECKING:
    from desktop.maestro_tab import MaestroTab


def group_style() -> str:
    return StyleFactory.group_box()


def build_stats_panel(tab: MaestroTab) -> QGroupBox:
    group = QGroupBox("Estado en tiempo real")
    group.setStyleSheet(StyleFactory.group_box())
    layout = QVBoxLayout(group)
    layout.setSpacing(6)

    tab._progress_bar = QProgressBar()
    tab._progress_bar.setRange(0, 100)
    tab._progress_bar.setValue(0)
    tab._progress_bar.setFormat("—")
    tab._progress_bar.setStyleSheet(StyleFactory.progress_bar())
    layout.addWidget(tab._progress_bar)

    tab.stat_labels = {}
    for key in ["subtasks_total", "elapsed_time", "tokens_used", "current_agent", "status"]:
        row = QHBoxLayout()
        label = QLabel(key.replace("_", " ").capitalize())
        label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        value = QLabel("—")
        value.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 12px; font-weight: bold;")
        row.addWidget(label)
        row.addStretch()
        row.addWidget(value)
        layout.addLayout(row)
        tab.stat_labels[key] = value
    return group


def build_execution_panel(tab: MaestroTab) -> QWidget:
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(8)

    layout.addWidget(build_stats_panel(tab))

    subtask_group = QGroupBox("Subtareas")
    subtask_group.setStyleSheet(group_style())
    sg_layout = QVBoxLayout(subtask_group)
    tab._subtask_list = QListWidget()
    tab._subtask_list.setStyleSheet(
        "QListWidget { background: #0F0F0F; border: 1px solid #2A2A2A; "
        "border-radius: 8px; padding: 4px; font-size: 11px; color: #A0A0A0; }"
        "QListWidget::item { padding: 3px 6px; }"
    )
    sg_layout.addWidget(tab._subtask_list)
    layout.addWidget(subtask_group, 1)

    files_group = QGroupBox("Archivos creados")
    files_group.setStyleSheet(group_style())
    fg_layout = QVBoxLayout(files_group)
    tab._files_written_list = QListWidget()
    tab._files_written_list.setStyleSheet(
        "QListWidget { background: #0F0F0F; border: 1px solid #2A2A2A; "
        "border-radius: 6px; font-size: 10px; color: #22C55E; }"
        "QListWidget::item { padding: 1px 6px; }"
    )
    fg_layout.addWidget(tab._files_written_list)
    layout.addWidget(files_group)
    return panel
