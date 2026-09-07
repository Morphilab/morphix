"""Debate Section — per-agent collapsible blocks embedded in chat."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import AGENT_PALETTE, COLORS

STATUS_ICONS = {
    "thinking": "⏳",
    "ready": "✅",
    "error": "⚠️",
}


class _AgentBlock(QWidget):
    """Collapsible block for a single agent's output.

    ``agent_name`` identifica al agente (encolado/estado); ``display_name``
    es lo que se muestra en el header (puede añadir el paso del workflow).
    """

    def __init__(self, agent_name: str, color: str, parent=None, display_name: str = ""):
        super().__init__(parent)
        self.agent_name = agent_name
        self.display_name = display_name or agent_name
        self.color = color
        self._collapsed = True

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # Toggle header
        self.toggle = QPushButton(f"🧑‍💻 {self.display_name.capitalize()}  ⏳  ▼")
        self.toggle.setStyleSheet(
            f"QPushButton {{ background: {COLORS['bg_surface_raised']}; "
            f"color: {color}; border: 1px solid {color}44; "
            f"border-radius: 6px; padding: 6px 10px; font-size: 12px; "
            f"font-weight: bold; text-align: left; }}"
            f"QPushButton:hover {{ background: {COLORS['border_default']}; }}"
        )
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.clicked.connect(self._toggle)
        main.addWidget(self.toggle)

        # Content frame (hidden by default)
        self.content = QFrame()
        self.content.setStyleSheet(
            f"background: {COLORS['bg_deepest']}; border-left: 3px solid {color}; "
            f"border-bottom: 1px solid {COLORS['border_default']}; "
            f"border-right: 1px solid {COLORS['border_default']}; "
            f"border-radius: 0 0 6px 6px; margin: 0 2px;"
        )
        self.content.setVisible(False)
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(8, 4, 8, 4)
        content_layout.setSpacing(2)

        self.browser = QTextBrowser()
        self.browser.setReadOnly(True)
        self.browser.setOpenExternalLinks(False)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {COLORS['text_primary']}; "
            f"border: none; font-size: 12px; }}"
        )
        self.browser.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
        )
        self.browser.setMinimumHeight(20)
        self.browser.document().contentsChanged.connect(self._adjust_height)
        content_layout.addWidget(self.browser)
        main.addWidget(self.content)

    def _toggle(self):
        self._collapsed = not self._collapsed
        self.content.setVisible(not self._collapsed)
        arrow = "▲" if not self._collapsed else "▼"
        # Rebuild header text preserving status icon
        current = self.toggle.text()
        status_part = ""
        for sym in STATUS_ICONS.values():
            if sym in current:
                status_part = f"  {sym}"
                break
        name_part = current.split("  ")[0] if "  " in current else current
        self.toggle.setText(f"{name_part}{status_part}  {arrow}")

    def append_text(self, chunk: str):
        self.browser.insertPlainText(chunk)

    def set_status(self, status: str):
        icon = STATUS_ICONS.get(status, "⏳")
        arrow = "▲" if not self._collapsed else "▼"
        self.toggle.setText(f"🧑‍💻 {self.display_name.capitalize()}  {icon}  {arrow}")

    def _adjust_height(self):
        doc = self.browser.document()
        doc.setTextWidth(self.browser.viewport().width())
        height = int(doc.size().height())
        if self.browser.minimumHeight() != height + 5:
            self.browser.setMinimumHeight(height + 5)

    def collapse(self):
        if not self._collapsed:
            self._toggle()

    def expand(self):
        if self._collapsed:
            self._toggle()


class DebateSection(QWidget):
    """Container for per-agent collapsible blocks in the chat flow.

    Los bloques se indexan por (agente, paso): en un loop DSL el mismo agente
    aparece en pasos distintos y cada uno merece su propio bloque — sin esto
    el diálogo de todo el run se acumulaba en un único bloque desorganizado.
    El estado (set_status) viaja sin paso y se aplica al bloque ACTIVO del
    agente (el último que empezó).
    """

    def __init__(self, title: str = "🤖 Actividad de agentes", parent=None):
        super().__init__(parent)
        self._blocks: dict[str, _AgentBlock] = {}
        self._active_by_agent: dict[str, str] = {}
        self._palette = list(AGENT_PALETTE)
        self._color_idx = 0

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(4)

        # Section header
        self._header = QLabel(title)
        self._header.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 13px; font-weight: bold; " f"padding: 2px 4px;"
        )
        main.addWidget(self._header)
        self._layout = main

    def set_title(self, title: str) -> None:
        self._header.setText(title)

    def _get_color(self, agent_name: str) -> str:
        for block in self._blocks.values():
            if block.agent_name == agent_name:
                return block.color
        color = self._palette[self._color_idx % len(self._palette)]
        self._color_idx += 1
        return color

    def _block_key(self, agent_name: str, label: str) -> str:
        return f"{agent_name}||{label or ''}"

    def start_agent(self, agent_name: str, label: str = ""):
        key = self._block_key(agent_name, label)
        if key not in self._blocks:
            color = self._get_color(agent_name)
            display = agent_name if not label else f"{agent_name} · {label}"
            block = _AgentBlock(agent_name, color, display_name=display)
            self._blocks[key] = block
            self._layout.addWidget(block)
        self._active_by_agent[agent_name] = key

    def append_chunk(self, agent_name: str, chunk: str, label: str = ""):
        self.start_agent(agent_name, label)
        self._blocks[self._block_key(agent_name, label)].append_text(chunk)

    def set_status(self, agent_name: str, status: str):
        active_key = self._active_by_agent.get(agent_name)
        if active_key in self._blocks:
            self._blocks[active_key].set_status(status)

    def clear(self):
        for block in self._blocks.values():
            self._layout.removeWidget(block)
            block.deleteLater()
        self._blocks.clear()
        self._active_by_agent.clear()
        self._color_idx = 0

    def is_empty(self) -> bool:
        return len(self._blocks) == 0
