"""Config Tab — modelos, herramientas, sistema."""

import datetime
import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import ACCENT, COLORS, StyleFactory

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
        self.models_text.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 13px;")
        self.models_text.setWordWrap(True)
        layout.addWidget(self.models_text)
        layout.addStretch()
        return w

    def _tools_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.tools_text = QLabel("Cargando...")
        self.tools_text.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 13px;")
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

        # Backlog 'polling eterno': patrón a-consumo (analytics) —
        # el monitor nace DETENIDO; corre solo por decisión del usuario.
        from PySide6.QtWidgets import QHBoxLayout, QPushButton

        row = QHBoxLayout()
        self.toggle_btn = QPushButton("▶ Actualizar")
        self.toggle_btn.setStyleSheet(StyleFactory.success_button())
        self.toggle_btn.clicked.connect(self._toggle_monitor)
        self.status_label = QLabel("○ detenido")
        self.status_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        row.addWidget(self.toggle_btn)
        row.addWidget(self.status_label)
        row.addStretch()
        layout.addLayout(row)

        layout.addStretch()

        # timer NACE detenido (sin _start_monitor en __init__)
        self._monitor_timer = QTimer(self)
        self._monitor_timer.timeout.connect(lambda: self._read_system_stats())
        self._last_monitor: datetime.datetime | None = None
        return w

    def _read_system_stats(self) -> None:
        import datetime as _dt

        import psutil

        self.cpu_bar.setValue(int(psutil.cpu_percent()))
        self.mem_bar.setValue(int(psutil.virtual_memory().percent))
        self._last_monitor = _dt.datetime.now()

    def _toggle_monitor(self) -> None:
        if self._monitor_timer.isActive():
            self._stop_monitor()
        else:
            self._start_monitor()

    def _start_monitor(self) -> None:
        if self._monitor_timer.isActive():  # guard doble-click
            return
        self._monitor_timer.start(3000)
        self.toggle_btn.setText("⏹ Detener")
        self.status_label.setText("● en vivo")
        self.status_label.setStyleSheet(
            f"color: {COLORS['success']}; font-size: 11px; font-weight: bold;"
        )
        self._read_system_stats()  # primera lectura inmediata

    def _stop_monitor(self) -> None:
        self._monitor_timer.stop()
        self.toggle_btn.setText("▶ Actualizar")
        last = f" · últ. {self._last_monitor.strftime('%H:%M:%S')}" if self._last_monitor else ""
        self.status_label.setText(f"○ detenido{last}")
        self.status_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")

    def hideEvent(self, event) -> None:  # noqa: N802 — API Qt
        # Auto-stop al salir del tab (defensa). Re-entrar NO re-arranca.
        if getattr(self, "_monitor_timer", None) is not None and self._monitor_timer.isActive():
            self._stop_monitor()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 — API Qt
        # No-op deliberado: el monitoreo solo arranca por decisión del usuario.
        super().showEvent(event)

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
