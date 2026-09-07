"""Main Window — PySide6 desktop GUI entry point."""

import asyncio
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

from desktop.async_helpers import run_async
from desktop.icons import get_icon
from desktop.theme import ACCENT, COLORS, StyleFactory, ThemeManager, get_dark_palette
from desktop.widgets.orbital_background import OrbitalBackground

# Sidebar items: (label, icon_name) — nombres de desktop/icons.py
_SIDEBAR_ITEMS = [
    ("Dashboard", "dashboard"),
    ("Maestro", "maestro"),
    ("Historial", "historial"),
    ("Editor", "editor"),
    ("Config", "config"),
    ("Analytics", "analytics"),
    ("Memoria", "memoria"),
    # el stack tiene index 7 = Bots (ver _load_real_tabs); sin este
    # item la página existía pero era inalcanzable desde la UI. INVARIANTE:
    # sidebar y _stacked se sincronizan POR ÍNDICE — toda página nueva
    # necesita su item aquí en la MISMA posición (guard: test_bots_tab_smoke).
    ("Bots", "bots"),
]


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Morphix")
        self.setFixedSize(400, 220)
        OrbitalBackground(draw_rules=False).attach(self)
        self._logged_in = False

        title = QLabel("Morphix")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"font-size: 28px; font-weight: bold; color: {ACCENT};"
            f" font-family: {ThemeManager.current().typography.family_serif};"
        )

        # Hairline bajo el título (wordmark serif)
        divider = QFrame()
        divider.setFixedSize(36, 1)
        divider.setStyleSheet(f"background: {COLORS['border_focus']}; border: none;")

        subtitle = QLabel("Tu equipo de agentes de software")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            f"font-size: 13px; color: {COLORS['text_secondary']};"
            f" font-family: {ThemeManager.current().typography.family_serif};"
            " font-style: italic;"
        )

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Contraseña maestra")
        self.password.setStyleSheet(StyleFactory.input_line())
        self.password.returnPressed.connect(self._login)

        self.login_btn = QPushButton("Iniciar Sesión")
        self.login_btn.setStyleSheet(StyleFactory.primary_button())
        self.login_btn.clicked.connect(self._login)

        self.error = QLabel("")
        self.error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error.setStyleSheet(f"color: {COLORS['error']}; font-size: 12px;")
        self.error.setVisible(False)
        # El error persiste hasta que el usuario vuelve a escribir (sin deadline)
        self.password.textChanged.connect(self.error.hide)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar { background: transparent; border: none; }"
            f"QProgressBar::chunk {{ background: {ACCENT}; }}"
        )
        self.progress.hide()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(40, 20, 40, 20)
        layout.addWidget(title)
        layout.addWidget(divider, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        layout.addWidget(self.password)
        layout.addWidget(self.login_btn)
        layout.addWidget(self.progress)
        layout.addWidget(self.error)

    def _login(self):
        try:
            import bcrypt
        except ImportError:
            self._show_error("Sistema no configurado: falta la librería bcrypt")
            return

        from core.config import settings

        password = self.password.text().strip()
        if not password:
            self._show_error("Ingresa una contraseña")
            return

        self.login_btn.setEnabled(False)
        self.login_btn.setText("Verificando...")
        self.password.setEnabled(False)
        self.progress.show()

        try:
            if not hasattr(settings, "password_hash") or not settings.password_hash:
                self._show_error("Sistema no configurado")
                self._reset_login_ui()
                return

            import concurrent.futures

            self._login_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            self._login_future = self._login_pool.submit(
                bcrypt.checkpw, password.encode(), settings.password_hash.encode()
            )
            self._poll_login()
        except Exception as e:
            logger.error(f"Error en login: {e}", exc_info=True)
            self._show_error(f"Error: {e!s}")
            self._reset_login_ui()

    def _poll_login(self):
        """Poll the bcrypt future without blocking the UI."""
        if self._login_future.done():
            try:
                result = self._login_future.result()
                if result:
                    self._logged_in = True
                    self.accept()
                else:
                    self._show_error("Contraseña incorrecta")
                    self._reset_login_ui()
            except Exception as e:
                logger.error(f"Error en login: {e}", exc_info=True)
                self._show_error(f"Error: {e!s}")
                self._reset_login_ui()
            finally:
                self._login_pool.shutdown(wait=False)
        else:
            QTimer.singleShot(50, self._poll_login)

    def _reset_login_ui(self):
        self.login_btn.setEnabled(True)
        self.login_btn.setText("Iniciar Sesión")
        self.password.setEnabled(True)
        self.progress.hide()

    def _show_error(self, msg: str):
        self.error.setText(msg)
        self.error.setVisible(True)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Morphix")
        self.setMinimumSize(1200, 750)
        self._init_task = None

        self._apply_dark_theme()
        self._build_menu()
        self._build_content_area()
        self._build_status_bar()

    def _build_content_area(self):
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        sidebar_widget = QWidget()
        sidebar_widget.setFixedWidth(200)
        sidebar_widget.setMinimumWidth(140)
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        logo = QLabel("Morphix")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {ACCENT}; letter-spacing: 2px; "
            f"padding: 16px 8px 12px 8px;"
        )
        sidebar_layout.addWidget(logo)

        self._sidebar = QListWidget()
        self._sidebar.setStyleSheet(StyleFactory.sidebar())
        for label, icon_name in _SIDEBAR_ITEMS:
            item = QListWidgetItem(f"  {label}")
            item.setIcon(get_icon(icon_name))
            item.setToolTip(f"Ver {label}")
            self._sidebar.addItem(item)
        self._sidebar.setCurrentRow(0)
        sidebar_layout.addWidget(self._sidebar, 1)

        # Pie del sidebar: selector global de workspace (spec §4)
        ws_foot = QWidget()
        ws_lay = QHBoxLayout(ws_foot)
        ws_lay.setContentsMargins(8, 6, 8, 10)
        ws_lay.setSpacing(4)
        self._ws_dot = QLabel("●")
        self._ws_dot.setStyleSheet(f"color: {COLORS['success']}; font-size: 11px;")
        self._ws_combo = QComboBox()
        self._ws_combo.setToolTip("Workspace activo")
        self._ws_new_btn = QPushButton()
        self._ws_new_btn.setIcon(get_icon("plus"))
        self._ws_new_btn.setStyleSheet(StyleFactory.small_button())
        self._ws_new_btn.setToolTip("Nuevo workspace")
        self._ws_new_btn.clicked.connect(self._create_workspace)
        self._ws_combo.currentTextChanged.connect(self._on_workspace_changed)
        ws_lay.addWidget(self._ws_dot)
        ws_lay.addWidget(self._ws_combo, 1)
        ws_lay.addWidget(self._ws_new_btn)
        sidebar_layout.addWidget(ws_foot)
        layout.addWidget(sidebar_widget)

        # Content (stacked widget)
        self._stacked = QStackedWidget()
        self.tabs = _StackedShim(self._stacked)

        loading = QLabel("Inicializando...")
        loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 16px;")
        self._loading_label = loading
        self.tabs.addTab(loading, "Maestro")
        self._stacked.setCurrentIndex(0)
        layout.addWidget(self._stacked, 1)

        # Sidebar navigation
        self._sidebar.currentRowChanged.connect(self._stacked.setCurrentIndex)
        OrbitalBackground().attach(central)
        self.setCentralWidget(central)

    def _apply_dark_theme(self):
        from core.config import settings

        if not settings.dark_mode:
            return
        palette = self.palette()
        for role, color in get_dark_palette(ThemeManager.current()).items():
            palette.setColor(QPalette.ColorGroup.All, role, QColor(color))
        self.setPalette(palette)
        self.setStyleSheet(StyleFactory.base_stylesheet())

    def _build_menu(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("Archivo")
        exit_action = QAction("Salir", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menu_bar.addMenu("Ayuda")
        about_action = QAction("Acerca de", self)
        about_action.triggered.connect(self._show_about)

        shortcuts_action = QAction("Atajos de teclado", self)
        shortcuts_action.triggered.connect(self._show_shortcuts)

        help_menu.addAction(about_action)
        help_menu.addAction(shortcuts_action)

    def _show_about(self):
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.about(
            self,
            "Acerca de Morphix",
            "Morphix v1.0.0\n\n"
            "Sistema de Razonamiento y Coordinación con IA.\n"
            "Arquitectura: PySide6 Desktop + CLI.\n"
            "Motor LLM: DeepSeek v4 + Ollama (offline).\n\n"
            "© 2026 MorphiLab",
        )

    def _show_shortcuts(self):
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            self,
            "Atajos de teclado",
            "Ctrl+Q       — Salir\n"
            "Ctrl+Enter   — Enviar mensaje en Maestro\n"
            "Shift+Enter  — Nueva línea en Maestro\n",
        )

    def _build_status_bar(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)

    async def init_backend(self):
        """Inicializa el backend y carga las pestañas reales."""
        from core.bootstrap import init_backend as do_init
        from core.bootstrap import start_daemons
        from core.config import settings

        success = await do_init(
            workspace=settings.active_workspace,
            on_progress=lambda msg: self.status.showMessage(msg, 3000),
        )
        if not success:
            self.status.showMessage("Error de inicialización", 0)
            return

        from desktop.events import _get_signals

        async def _on_offline_changed(offline: bool):
            _get_signals().offline_changed.emit(offline)

        await start_daemons(on_offline_changed=_on_offline_changed)

        # Despertador machine-local del inbox entre bots
        from core.bootstrap import register_daemon
        from orchestration.bots_wake import bots_wake_loop

        register_daemon(bots_wake_loop())

        # Reloj de rutinas (entrega dual history/bot-chat)
        from orchestration.bots_clock import routines_loop

        register_daemon(routines_loop())

        # Load real tabs
        self._load_real_tabs()

    def _load_real_tabs(self):
        # Liberar el QLabel de carga
        if hasattr(self, "_loading_label") and self._loading_label is not None:
            self._loading_label.deleteLater()
            self._loading_label = None
        self.tabs.clear()

        from desktop.analytics_tab import AnalyticsTab
        from desktop.config_tab import ConfigTab
        from desktop.dashboard_tab import DashboardTab
        from desktop.editor_tab import EditorTab
        from desktop.history_tab import HistoryTab
        from desktop.maestro_tab import MaestroTab

        self.tabs.addTab(DashboardTab(), "Dashboard")  # index 0
        maestro = MaestroTab()
        self.maestro = maestro
        self.tabs.addTab(maestro, "Maestro")  # index 1
        history = HistoryTab()
        self.history = history
        self.tabs.addTab(history, "Historial")  # index 2
        history.conversation_selected.connect(self._on_resume_conversation)
        # eliminar desde Historial invalida el conv_id en las panes.
        history.conversation_deleted.connect(maestro.forget_conversation)
        editor = EditorTab()
        self.editor = editor
        self.tabs.addTab(editor, "Editor")  # index 3
        self.tabs.addTab(ConfigTab(), "Config")  # index 4
        self.tabs.addTab(AnalyticsTab(), "Analytics")  # index 5

        # Tab Memoria: inspección/borrado de memoria.
        from desktop.memoria_tab import MemoriaTab

        self.tabs.addTab(MemoriaTab(), "Memoria")  # index 6; Bots pasa a 7

        # Bot Mode (contrato 08): pestaña Bots al final; el chat eterno
        # abierto desde una fila viaja por _on_resume_conversation.
        from desktop.bots_tab import BotsTab

        bots_tab = BotsTab()
        self.bots = bots_tab
        self.tabs.addTab(bots_tab, "Bots")  # index 7
        bots_tab.open_conversation.connect(self._on_resume_conversation)

        # Sync sidebar → content
        self._sidebar.setCurrentRow(0)

        from core.workspaces import get_global_workspaces

        self.status.showMessage("✅ Morphix listo", 5000)

        from desktop.events import get_signals

        get_signals().offline_changed.connect(
            lambda offline: self.status.showMessage(
                f"⚠️ Modo offline {'activado' if offline else 'desactivado'}", 8000
            )
        )
        get_signals().offline_changed.connect(self._refresh_offline_dot)

        from core.config import settings

        self._refresh_offline_dot(settings.offline_mode)

        def _on_ws_change(_ws):
            self._refresh_workspaces()
            self._check_skills_approval(_ws)

        get_signals().workspace_changed.connect(_on_ws_change)
        get_signals().project_changed.connect(
            lambda root: self.editor.set_project(root or None, get_global_workspaces().current)
        )
        get_signals().open_file_requested.connect(self._on_open_file_requested)
        get_signals().view_file_requested.connect(self._on_view_file_requested)
        editor.set_project(maestro._current_project_root, get_global_workspaces().current)
        self._refresh_workspaces()

    # ── Workspace: selector global en el pie del sidebar (movido desde Dashboard) ──

    def _refresh_offline_dot(self, offline: bool):
        """Dot global del sidebar: ● verde online · ● ámbar offline (visible en todas las tabs)."""
        self._ws_dot.setText("●")
        self._ws_dot.setStyleSheet(
            f"color: {COLORS['warning'] if offline else COLORS['success']}; font-size: 11px;"
        )
        self._ws_dot.setToolTip("Modo offline" if offline else "Online")

    def _refresh_workspaces(self):
        from core.workspaces import get_global_workspaces

        async def _load():
            ws = get_global_workspaces()
            schemas = await ws.list_workspaces()
            self._ws_combo.blockSignals(True)
            self._ws_combo.clear()
            self._ws_combo.addItems(schemas)
            self._ws_combo.setCurrentText(ws.current)
            self._ws_combo.blockSignals(False)

        run_async(_load())

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
            from desktop.events import get_signals

            self._ws_new_btn.setEnabled(False)
            try:
                ws = get_global_workspaces()
                schemas = await ws.list_workspaces()

                if name in schemas:
                    switched = await switch_workspace_handler(name)
                else:
                    switched = await ws.create_workspace(name)

                if not switched:
                    # vetado (workflow activo) o switch fallido — no emitir
                    QMessageBox.information(
                        self,
                        "Cambio bloqueado",
                        f"No se puede cambiar a '{name}' mientras hay un workflow en ejecución.",
                    )
                    return

                get_signals().workspace_changed.emit(name)
            finally:
                self._ws_new_btn.setEnabled(True)
                self._refresh_workspaces()

        run_async(_create())

    def _check_skills_approval(self, ws_name: str) -> None:
        """Skills locales nuevas o modificadas piden aprobación.

        Sync deliberado: son lecturas locales pequeñas (<10 archivos). Sin
        pendientes el método es no-op (nunca bloquea el cambio de workspace).
        """
        if not ws_name:
            return
        try:
            from core.skills import SkillNotFoundError, load_skill
            from core.skills_approval import approval_status

            pendings: list[tuple[str, str, str]] = []
            for name, st in sorted(approval_status(ws_name).items()):
                if st not in ("pending", "changed"):
                    continue
                try:
                    skill = load_skill(name, ws_name)
                except SkillNotFoundError:
                    continue
                pendings.append((name, st, skill.body))
            if not pendings:
                return
            from desktop.widgets.skills_approval_dialog import SkillsApprovalDialog

            SkillsApprovalDialog(ws_name, pendings, parent=self).exec()
        except Exception:
            logging.getLogger(__name__).warning("revisión de skills locales falló", exc_info=True)

    def _on_workspace_changed(self, name: str):
        if not name:
            return

        async def _switch():
            from PySide6.QtWidgets import QMessageBox

            from core.workspaces import get_global_workspaces, switch_workspace_handler

            ok = await switch_workspace_handler(name)
            if not ok:
                # vetado (workflow activo) — revertir combo y avisar
                current = get_global_workspaces().current or "main"
                QMessageBox.information(
                    self,
                    "Cambio bloqueado",
                    f"No se puede cambiar a '{name}' mientras hay un workflow en ejecución.",
                )
                self._ws_combo.blockSignals(True)
                self._ws_combo.setCurrentText(current)
                self._ws_combo.blockSignals(False)
                return
            from desktop.events import get_signals

            get_signals().workspace_changed.emit(name)

        run_async(_switch())

    def _on_open_file_requested(self, rel_path: str):
        """Doble clic en 'Archivos creados' → abre el archivo en el Editor."""
        self.tabs.setCurrentIndex(3)  # Editor
        self._sidebar.setCurrentRow(3)
        self.editor.open_project_file(rel_path)

    def _on_view_file_requested(self, rel_path: str):
        """Visor standalone: doble-click en archivos / links view-file://."""
        from pathlib import Path

        from PySide6.QtWidgets import QMessageBox

        from desktop.services.file_viewer_service import open_in_viewer

        root = self.maestro._current_project_root or "."
        candidate = Path(root) / rel_path if not Path(rel_path).is_absolute() else Path(rel_path)
        if not open_in_viewer(candidate):
            QMessageBox.information(
                self,
                "Visor",
                f"No se pudo abrir el visor para:\n{rel_path}",
            )

    def _on_resume_conversation(self, conv_id: int):
        """Load conversation into Maestro tab and switch to it."""
        run_async(self.maestro.load_conversation(conv_id))
        self._stacked.setCurrentWidget(self.maestro)
        self._sidebar.setCurrentRow(1)  # Maestro sidebar index

    def closeEvent(self, event):
        """Shutdown limpio: cancelar tareas, daemons, cerrar pool de BD."""
        import time

        from PySide6.QtCore import QTimer

        if getattr(self, "_shutting_down", False):
            event.accept()
            return
        self._shutting_down = True

        logger.info("Cerrando aplicación...")
        if hasattr(self, "_init_task") and self._init_task is not None:
            self._init_task.cancel()
        try:
            from core.bootstrap import stop_daemons
            from core.database import dispose_engine

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
            if loop.is_running():
                t_stop = run_async(stop_daemons(), loop=loop)
                t_dispose = run_async(dispose_engine(), loop=loop)
                _deadline = [time.monotonic() + 3]

                def _check_shutdown():
                    if t_stop.done() and t_dispose.done():
                        event.accept()
                    elif time.monotonic() < _deadline[0]:
                        QTimer.singleShot(10, _check_shutdown)
                    else:
                        logger.warning("Shutdown timed out, forzando cierre")
                        event.accept()

                _check_shutdown()
                return  # closeEvent completes asynchronously via _check_shutdown
        except RuntimeError:
            logger.debug("Event loop ya cerrado, omitiendo shutdown asíncrono")
        except Exception as e:
            logger.debug(f"Error en shutdown: {e}")
        event.accept()


class _StackedShim:
    """Backward-compat wrapper so code that accesses QTabWidget-like API still works."""

    def __init__(self, stacked: QStackedWidget):
        self._stacked = stacked
        self._labels: dict[int, str] = {}

    def addTab(self, widget: QWidget, label: str) -> int:
        idx = self._stacked.addWidget(widget)
        self._labels[idx] = label
        return idx

    def clear(self) -> None:
        self._labels.clear()
        while self._stacked.count() > 0:
            w = self._stacked.widget(0)
            if w is not None:
                self._stacked.removeWidget(w)

    def setCurrentWidget(self, widget: QWidget) -> None:
        self._stacked.setCurrentWidget(widget)

    def setCurrentIndex(self, index: int) -> None:
        self._stacked.setCurrentIndex(index)

    def currentIndex(self) -> int:
        return self._stacked.currentIndex()

    def tabText(self, index: int) -> str:
        return self._labels.get(index, "")

    def widget(self, index: int) -> QWidget | None:
        return self._stacked.widget(index)

    def count(self) -> int:
        return self._stacked.count()
