"""Agent Panel — dynamically shows per-agent responses grouped by name."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import AGENT_PALETTE, COLORS


class AgentPanel(QWidget):
    """Dynamic panel with per-agent responses, grouped by agent name.

    Each agent gets a QGroupBox with rotating color. Responses are
    appended as QLabels inside the group.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: dict[str, tuple[QGroupBox, QVBoxLayout, str]] = {}
        self._palette = list(AGENT_PALETTE)
        self._color_idx = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {COLORS['bg_deepest']}; border: none; }}"
        )

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setSpacing(6)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.addStretch()

        self._scroll.setWidget(self._container)
        outer.addWidget(self._scroll)

    def _get_color(self, agent_name: str) -> str:
        """Return consistent color for an agent. First-seen gets next palette slot."""
        if agent_name in self._groups:
            return self._groups[agent_name][2]
        color = self._palette[self._color_idx % len(self._palette)]
        self._color_idx += 1
        return color

    def add_response(self, agent_name: str, label: str, text: str):
        """Add a response entry for an agent. Creates the group if new."""
        if agent_name not in self._groups:
            color = self._get_color(agent_name)
            group = QGroupBox(agent_name.capitalize())
            group.setStyleSheet(
                f"QGroupBox {{ color: {color}; font-weight: bold; border: 1px solid {color}44; "
                f"border-radius: 6px; margin-top: 8px; padding-top: 12px; font-size: 12px; }}"
                f"QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}"
            )
            inner = QVBoxLayout(group)
            inner.setSpacing(4)
            inner.setContentsMargins(8, 8, 8, 8)

            # Insert before the stretch
            self._layout.insertWidget(self._layout.count() - 1, group)
            self._groups[agent_name] = (group, inner, color)

        _, inner, _ = self._groups[agent_name]
        entry = QLabel(f"<b>{label}:</b> {text}")
        entry.setWordWrap(True)
        entry.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        entry.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 12px; padding: 2px 0;")
        inner.addWidget(entry)

        self._scroll_to_bottom()

    def clear(self):
        """Remove all agent groups."""
        for group, _, _ in self._groups.values():
            self._layout.removeWidget(group)
            group.deleteLater()
        self._groups.clear()

    def is_empty(self) -> bool:
        return len(self._groups) == 0

    def update_files_written(self, files: list[str]):
        """Update the files-written indicator at the top of the panel."""
        if not hasattr(self, "_files_label"):
            from PySide6.QtWidgets import QListWidget

            self._files_list = QListWidget()
            self._files_list.setMaximumHeight(80)
            self._files_list.setStyleSheet(
                f"QListWidget {{ background: {COLORS['bg_deepest']}; "
                f"border: 1px solid {COLORS['border_default']}; "
                f"border-radius: 4px; font-size: 11px; color: {COLORS['success']}; }}"
            )
            self._layout.insertWidget(0, self._files_list)
        self._files_list.clear()
        for f in files[:10]:
            self._files_list.addItem(f"  {f}")

    def _scroll_to_bottom(self):
        sb = self._scroll.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())
