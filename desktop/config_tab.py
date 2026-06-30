"""Config Tab — modelos, herramientas, sistema."""

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import ACCENT, StyleFactory

logger = logging.getLogger(__name__)

from desktop.async_helpers import run_async


class ConfigTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        run_async(self._refresh())

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(12, 12, 12, 12)

        self.inner_tabs = QTabWidget()
        self.inner_tabs.setStyleSheet(StyleFactory.tab_widget())

        self.inner_tabs.addTab(self._models_tab(), "Modelos")
        self.inner_tabs.addTab(self._tools_tab(), "Herramientas")
        self.inner_tabs.addTab(self._system_tab(), "Sistema")

        main.addWidget(self.inner_tabs)

    def _models_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.models_text = QLabel("Cargando...")
        self.models_text.setStyleSheet("color: #E5E5E5; font-size: 13px;")
        self.models_text.setWordWrap(True)
        layout.addWidget(self.models_text)
        layout.addStretch()
        return w

    def _tools_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.tools_text = QLabel("Cargando...")
        self.tools_text.setStyleSheet("color: #E5E5E5; font-size: 13px;")
        self.tools_text.setWordWrap(True)
        layout.addWidget(self.tools_text)
        layout.addStretch()
        return w

    def _system_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        self.cpu_bar = QProgressBar()
        self.cpu_bar.setStyleSheet(StyleFactory.progress_bar(ACCENT))
        self.mem_bar = QProgressBar()
        self.mem_bar.setStyleSheet(self.cpu_bar.styleSheet())

        layout.addWidget(QLabel("CPU"))
        layout.addWidget(self.cpu_bar)
        layout.addWidget(QLabel("Memoria RAM"))
        layout.addWidget(self.mem_bar)
        layout.addStretch()

        self._start_monitor()
        return w

    async def _refresh(self):
        try:
            from core.config import settings as s
            from tools.specs import TOOL_DEFINITIONS

            models = []
            for role, cfg in s.model_roles.items():
                models.append(
                    f"• {role}: {cfg['provider']} / {cfg['model']} (T={cfg['temperature']})"
                )
            models.append(f"• Ollama: {s.ollama_model} @ {s.ollama_base_url}")
            models.append(f"• Timeout: {s.llm_timeout}s")
            self.models_text.setText("\n".join(models))

            tools = [f"🔧 {len(TOOL_DEFINITIONS)} herramientas:"]
            for name, td in TOOL_DEFINITIONS.items():
                tools.append(f"  • {name} — {td.description[:80]}...")
            self.tools_text.setText("\n".join(tools))
        except Exception as e:
            logger.exception("Error cargando configuración")
            self.models_text.setText(f"Error: {e}")

    def _start_monitor(self):
        import psutil

        def _update():
            self.cpu_bar.setValue(int(psutil.cpu_percent()))
            self.mem_bar.setValue(int(psutil.virtual_memory().percent))

        self._monitor_timer = QTimer(self)
        self._monitor_timer.timeout.connect(_update)
        self._monitor_timer.start(3000)
