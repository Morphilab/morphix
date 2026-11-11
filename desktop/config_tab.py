"""Config Tab — modelos, herramientas, sistema."""

import datetime
import logging

from PySide6.QtCore import (
    Qt,  # noqa: E402 — usado por UserRole en el manager
    QTimer,
)
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
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
        self.inner_tabs.addTab(self._appearance_tab(), "Apariencia")
        self.inner_tabs.addTab(self._skills_tab(), "Skills")

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

    def _skills_tab(self) -> QWidget:
        """Manager permanente de skills del workspace (aprobación local)."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Skills del workspace — estado de aprobación"))

        self.skills_list = QListWidget()
        self.skills_list.setStyleSheet(StyleFactory.list_widget())
        layout.addWidget(self.skills_list)

        from PySide6.QtWidgets import QHBoxLayout as _H
        from PySide6.QtWidgets import QPushButton as _B

        row = _H()
        btn_refresh = _B("⟳ Refrescar")
        btn_refresh.setStyleSheet(StyleFactory.secondary_button())
        btn_refresh.clicked.connect(self._refresh_skills)
        btn_approve = _B("✔ Aprobar")
        btn_approve.setStyleSheet(StyleFactory.success_button())
        btn_approve.clicked.connect(lambda: self._act_on_skill(aprobar=True))
        btn_reject = _B("✖ Rechazar")
        btn_reject.setStyleSheet(StyleFactory.danger_button())
        btn_reject.clicked.connect(lambda: self._act_on_skill(aprobar=False))
        for b in (btn_refresh, btn_approve, btn_reject):
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)
        self.skills_status = QLabel("")
        self.skills_status.setStyleSheet(f"color:{COLORS['text_secondary']}; font-size: 11px;")
        layout.addWidget(self.skills_status)
        layout.addStretch()
        return w

    def _refresh_skills(self) -> None:
        from core.workspaces import get_global_workspaces
        from desktop.services.skills_service import list_workspace_skills

        ws = get_global_workspaces().current
        self.skills_list.clear()
        try:
            filas = list_workspace_skills(ws)
        except Exception as e:  # noqa: BLE001 — la lectura no debe tumbar el tab
            self.skills_status.setText(f"⚠ no se pudieron leer las skills: {e}")
            return
        for f in filas:
            item = QListWidgetItem(f"{f['icon']} {f['name']} — {f['state']}")
            item.setData(Qt.ItemDataRole.UserRole, f["name"])
            self.skills_list.addItem(item)
        self.skills_status.setText(f"{len(filas)} skill(s) en el workspace '{ws}'")

    def _act_on_skill(self, *, aprobar: bool) -> None:
        from core.workspaces import get_global_workspaces
        from desktop.services import skills_service as svc

        item = self.skills_list.currentItem()
        if item is None:
            self.skills_status.setText("Selecciona una skill primero.")
            return
        nombre = item.data(Qt.ItemDataRole.UserRole)
        ws = get_global_workspaces().current
        ok = (
            svc.approve_workspace_skill(ws, nombre)
            if aprobar
            else svc.reject_workspace_skill(ws, nombre)
        )
        self.skills_status.setText(
            f"{'✔ aprobada' if aprobar else '✖ rechazada'}: {nombre}" if ok else "⚠ no aplicado"
        )
        self._refresh_skills()

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

        # Salud del proveedor LLM: última latencia y fallos consecutivos —
        # un proveedor degradado era invisible hasta que el paso moría.
        self.provider_label = QLabel("Proveedor: —")
        self.provider_label.setStyleSheet(f"color:{COLORS['text_secondary']}; font-size: 11px;")
        layout.addWidget(self.provider_label)

        # Patrón a-consumo (analytics):
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
        try:
            from core.provider_health import snapshot

            s = snapshot()
            if not s.get("provider"):
                texto = "Proveedor: — (sin llamadas aún)"
            elif s.get("consecutive_failures"):
                texto = (
                    f"Proveedor: {s['provider']} · ⚠ {s['consecutive_failures']} fallo(s) "
                    f"consecutivo(s): {str(s.get('last_error') or '')[:60]}"
                )
            else:
                lat = s.get("last_latency_s")
                lat_s = f" · últ. {float(lat):.1f}s" if lat else ""
                texto = f"Proveedor: {s['provider']} · OK{lat_s}"
            self.provider_label.setText(texto)
        except Exception:
            self.provider_label.setText("Proveedor: —")
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

    def _appearance_tab(self) -> QWidget:
        """Selector de tema: dos modos (oscuro papel / claro papel claro).

        El tema se resuelve al arrancar: el toggle persiste en .env y avisa
        honestamente que la aplicación efectiva es al reiniciar.
        """
        from PySide6.QtWidgets import QHBoxLayout, QPushButton

        from core.config import settings

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Tema de la interfaz"))
        hint = QLabel("Oscuro (papel) o claro (papel claro). Se aplica al reiniciar la aplicación.")
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        self.theme_dark_btn = QPushButton("Oscuro · papel")
        self.theme_light_btn = QPushButton("Claro · papel claro")
        self.theme_dark_btn.clicked.connect(lambda: self._set_theme(True))
        self.theme_light_btn.clicked.connect(lambda: self._set_theme(False))
        row.addWidget(self.theme_dark_btn)
        row.addWidget(self.theme_light_btn)
        row.addStretch()
        layout.addLayout(row)

        self.theme_status = QLabel("")
        self.theme_status.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        layout.addWidget(self.theme_status)
        layout.addStretch()

        self._refresh_theme_buttons(settings.dark_mode)
        return w

    def _refresh_theme_buttons(self, dark: bool) -> None:
        self.theme_dark_btn.setStyleSheet(
            StyleFactory.toggle_active() if dark else StyleFactory.toggle_inactive()
        )
        self.theme_light_btn.setStyleSheet(
            StyleFactory.toggle_inactive() if dark else StyleFactory.toggle_active()
        )

    def _set_theme(self, dark: bool) -> None:
        from desktop.services.config_service import ConfigService

        result = ConfigService.set_dark_mode(dark)
        applied = bool(result.get("dark_mode", dark))
        self._refresh_theme_buttons(applied)
        modo = "oscuro (papel)" if applied else "claro (papel claro)"
        self.theme_status.setText(f"Guardado: {modo} — se aplica al reiniciar la aplicación.")

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
