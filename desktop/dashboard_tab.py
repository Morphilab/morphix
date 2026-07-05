"""Dashboard Tab — workspace, workflow, navegación, métricas, offline, self-reflection."""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import ACCENT, COLORS, StyleFactory

logger = logging.getLogger(__name__)

from desktop.async_helpers import run_async


class DashboardTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._connect_signals()
        run_async(self._load_data())

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(20, 16, 20, 16)
        main.setSpacing(12)

        title = QLabel("Bienvenido a Morphix")
        title.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {ACCENT};")
        main.addWidget(title)
        main.addSpacing(8)

        # Navigation cards — Workflows + Dynamic agents (full width)
        modules_group = QGroupBox("Módulos")
        modules_group.setStyleSheet(StyleFactory.group_box())
        modules_layout = QVBoxLayout(modules_group)
        modules_layout.setSpacing(12)

        # Workspace selector at top of modules
        ws_row = QHBoxLayout()
        ws_row.setSpacing(6)
        self.workspace_combo = QComboBox()
        self.workspace_combo.setStyleSheet(
            f"QComboBox {{ background: {COLORS['bg_surface']}; "
            f"color: {COLORS['text_primary']}; border: 1px solid {COLORS['border_default']}; "
            f"border-radius: 4px; padding: 6px; }}"
        )
        ws_row.addWidget(self.workspace_combo, 1)
        self.new_ws_btn = QPushButton("+ Nuevo")
        self.new_ws_btn.setStyleSheet(StyleFactory.secondary_button())
        self.new_ws_btn.clicked.connect(self._create_workspace)
        ws_row.addWidget(self.new_ws_btn)
        modules_layout.addLayout(ws_row)

        # Workflows
        wf_group = QGroupBox("Workflows")
        wf_group.setStyleSheet(
            "QGroupBox { color: #E5E5E5; font-weight: bold; border: none;"
            "margin-top: 4px; padding-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 0px; }"
        )
        self.workflows_layout = QVBoxLayout(wf_group)
        self.workflows_layout.setSpacing(4)
        modules_layout.addWidget(wf_group)

        # Agentes
        ag_group = QGroupBox("Agentes")
        ag_group.setStyleSheet(wf_group.styleSheet())
        self.dash_agents_layout = QGridLayout(ag_group)
        self.dash_agents_layout.setSpacing(6)
        modules_layout.addWidget(ag_group)

        main.addWidget(modules_group, 1)

        # Bottom row — mode, self-reflection, logs
        log_btn_style = StyleFactory.secondary_button()
        log_row = QHBoxLayout()

        # Mode indicator (icon + toggle)
        self.mode_icon = QLabel("☁")
        self.mode_icon.setStyleSheet("font-size: 18px;")
        log_row.addWidget(self.mode_icon)
        self.offline_btn = QPushButton("Activar Offline")
        self.offline_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: #FFF; border-radius: 6px; padding: 6px 12px; font-size: 11px; }}"
        )
        self.offline_btn.clicked.connect(self._toggle_offline)
        log_row.addWidget(self.offline_btn)

        # Self-reflection
        self.self_reflection_cb = QCheckBox("Self-Reflection")
        self.self_reflection_cb.setToolTip("Agentes se auto-revisan")
        self.self_reflection_cb.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 12px;"
        )
        self.self_reflection_cb.toggled.connect(self._toggle_self_reflection)
        log_row.addWidget(self.self_reflection_cb)

        log_row.addStretch()
        open_logs_btn = QPushButton("Abrir Logs")
        open_logs_btn.setStyleSheet(log_btn_style)
        open_logs_btn.clicked.connect(self._open_logs)
        open_lnav_btn = QPushButton("lnav")
        open_lnav_btn.setStyleSheet(log_btn_style)
        open_lnav_btn.clicked.connect(self._open_logs_lnav)
        dark_btn = QPushButton("Tema")
        dark_btn.setStyleSheet(log_btn_style)
        dark_btn.clicked.connect(self._toggle_theme)
        restart_btn = QPushButton("Reiniciar")
        restart_btn.setStyleSheet(log_btn_style)
        restart_btn.clicked.connect(self._restart_app)
        log_row.addWidget(open_logs_btn)
        log_row.addWidget(open_lnav_btn)
        log_row.addWidget(dark_btn)
        log_row.addWidget(restart_btn)
        main.addLayout(log_row)

        main.addStretch()

    def _connect_signals(self):
        self.workspace_combo.currentTextChanged.connect(self._on_workspace_changed)
        from desktop.events import get_signals

        get_signals().offline_changed.connect(lambda offline: self._refresh_offline_indicators())
        get_signals().workspace_changed.connect(self._on_external_workspace_change)

    async def _load_data(self):
        try:
            from core.workspaces import get_global_workspaces

            ws = get_global_workspaces()
            schemas = await ws.list_workspaces()

            self.workspace_combo.blockSignals(True)
            self.workspace_combo.clear()
            self.workspace_combo.addItems(schemas)
            self.workspace_combo.setCurrentText(ws.current)
            self.workspace_combo.blockSignals(False)

            from core.feature_flags import kairos

            self.self_reflection_cb.blockSignals(True)
            self.self_reflection_cb.setChecked(kairos.get("AGENT_SELF_REFLECTION", False))
            self.self_reflection_cb.blockSignals(False)

            self._refresh_offline_indicators()
            self._refresh_modules()

        except Exception:
            logger.exception("Error cargando datos del dashboard")

    def _refresh_modules(self):
        """Repuebla las cards de Workflows y Agentes dinámicamente."""
        from agents.registry import agents_registry
        from core.workspaces import get_global_workspaces
        from orchestration.loader import list_workflows, load_workflow_template

        ws = get_global_workspaces().current

        card_style = StyleFactory.card_button()

        # Repopulate Workflows
        while self.workflows_layout.count():
            item = self.workflows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        workflows = list_workflows(ws)
        for wf_name in workflows:
            template = load_workflow_template(ws, wf_name)
            desc = template.get("description", "") if template else ""
            label = f"{wf_name}"
            if desc:
                label += f"  —  {desc[:80]}{'...' if len(desc) > 80 else ''}"

            btn = QPushButton(label)
            btn.setStyleSheet(card_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if desc:
                btn.setToolTip(desc[:200])
            btn.clicked.connect(
                lambda checked, n=wf_name: self._navigate("maestro", {"workflow": n})
            )
            self.workflows_layout.addWidget(btn)

        # Repopulate Agentes
        while self.dash_agents_layout.count():
            item = self.dash_agents_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        registered = agents_registry.list_agents()
        col = 0
        row = 0
        for agent_name in sorted(registered.keys()):
            profile = agents_registry.get_profile(agent_name)
            tools = profile.get("tools", []) if profile else []
            tool_info = f" ({len(tools)} tools)" if tools else ""
            label = f"{agent_name.capitalize()}{tool_info}"

            btn = QPushButton(label)
            btn.setStyleSheet(StyleFactory.card_button())
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda checked, n=agent_name: self._navigate("maestro", {"agent": n})
            )
            self.dash_agents_layout.addWidget(btn, row, col)

            col += 1
            if col >= 2:
                col = 0
                row += 1

    def _create_workspace(self):
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        name, ok = QInputDialog.getText(
            self,
            "Nuevo Workspace",
            "Nombre del workspace (minúsculas, números, _):",
            text="",
        )
        if not ok or not name:
            return
        name = name.strip().lower().replace(" ", "_")
        if not name or not name[0].isalpha():
            QMessageBox.warning(self, "Inválido", "El nombre debe empezar con letra (a-z).")
            return

        import re

        if not re.match(r"^[a-z][a-z0-9_]*$", name):
            QMessageBox.warning(self, "Inválido", "Solo minúsculas, números y guiones bajos.")
            return

        async def _create():
            from core.workspaces import get_global_workspaces, switch_workspace_handler

            ws = get_global_workspaces()
            schemas = await ws.list_workspaces()

            if name in schemas:
                self.new_ws_btn.setEnabled(False)
                self.new_ws_btn.setText("Cargando...")
                await switch_workspace_handler(name)
                await self._load_data()
                from desktop.events import get_signals

                get_signals().workspace_changed.emit(name)
                self.new_ws_btn.setEnabled(True)
                self.new_ws_btn.setText("+ Nuevo")
                return

            self.new_ws_btn.setEnabled(False)
            self.new_ws_btn.setText("Creando...")
            await ws.create_workspace(name)
            await self._load_data()
            from desktop.events import get_signals

            get_signals().workspace_changed.emit(name)
            self.new_ws_btn.setEnabled(True)
            self.new_ws_btn.setText("+ Nuevo")

        run_async(_create())

    def _on_workspace_changed(self, name: str):
        if not name:
            return

        async def _switch():
            from core.workspaces import switch_workspace_handler

            await switch_workspace_handler(name)
            await self._load_data()
            from desktop.events import get_signals

            get_signals().workspace_changed.emit(name)

        run_async(_switch())

    def _on_external_workspace_change(self, name: str):
        """Refresh dashboard when workspace changes from another tab."""
        if name and name != self.workspace_combo.currentText():
            self.workspace_combo.blockSignals(True)
            self.workspace_combo.setCurrentText(name)
            self.workspace_combo.blockSignals(False)
            run_async(self._load_data())

    def _toggle_offline(self):
        from core.config import settings
        from desktop.services.config_service import ConfigService

        ConfigService.toggle_offline_mode()
        self._refresh_offline_indicators()
        from desktop.events import get_signals

        get_signals().offline_changed.emit(settings.offline_mode)

    def _refresh_offline_indicators(self):
        """Actualiza los indicadores de estado offline sin recargar todo."""
        from core.config import settings as s

        is_off = s.offline_mode
        self.mode_icon.setText("☁" if not is_off else "⛔")
        self.mode_icon.setStyleSheet(
            f"font-size: 18px; color: {'#22C55E' if not is_off else '#F59E0B'};"
        )
        self.offline_btn.setText("Desactivar Offline" if is_off else "Activar Offline")

    def _toggle_self_reflection(self, enabled: bool):
        from core.feature_flags import kairos

        kairos.set("AGENT_SELF_REFLECTION", enabled)

    def _open_logs(self):
        from desktop.services.dashboard_service import DashboardService

        result = DashboardService.open_logs()
        if not result.get("success"):
            parent = self.window()
            if parent and hasattr(parent, "status"):
                parent.status.showMessage(f"Error abriendo logs: {result.get('message', '')}", 5000)

    def _open_logs_lnav(self):
        from desktop.services.dashboard_service import DashboardService

        result = DashboardService.open_logs_lnav()
        if not result.get("success"):
            parent = self.window()
            if parent and hasattr(parent, "status"):
                parent.status.showMessage(f"Error con lnav: {result.get('message', '')}", 5000)

    def _toggle_theme(self):
        from core.config import settings
        from desktop.services.config_service import ConfigService

        ConfigService.toggle_dark_mode(not settings.dark_mode)
        parent = self.window()
        if parent and hasattr(parent, "status"):
            parent.status.showMessage(
                f"Tema {'oscuro' if settings.dark_mode else 'claro'} (reinicia para aplicar)", 5000
            )

    def _restart_app(self):
        from desktop.services.config_service import ConfigService

        result = ConfigService.restart_application()
        if not result.get("success"):
            parent = self.window()
            if parent and hasattr(parent, "status"):
                parent.status.showMessage(f"Error al reiniciar: {result.get('message', '')}", 5000)

    def _navigate(self, route: str, context: dict | None = None):
        parent = self.window()
        if parent and hasattr(parent, "tabs"):
            tabs = parent.tabs
            tab_map = {
                "maestro": "Maestro",
                "historial": "Historial",
                "integraciones": "Integraciones",
                "configuración": "Config",
                "analytics": "Analytics",
            }
            target = tab_map.get(route)
            if target:
                for i in range(tabs.count()):
                    if tabs.tabText(i) == target:
                        widget = tabs.widget(i)
                        if route == "maestro" and context and hasattr(widget, "launch_workflow"):
                            if "workflow" in context:
                                widget.launch_workflow(context["workflow"])
                            elif "agent" in context:
                                widget.launch_agent(context["agent"])
                        tabs.setCurrentIndex(i)
                        break
