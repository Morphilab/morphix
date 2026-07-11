"""Main Window — PySide6 desktop GUI entry point."""

import asyncio
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QDialog,
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
from desktop.theme import ACCENT, COLORS, StyleFactory, get_dark_palette

# Sidebar items: (label, icon_unicode)
_SIDEBAR_ITEMS = [
    ("Dashboard", "🏠"),
    ("Maestro", "💬"),
    ("Historial", "🕐"),
    ("Editor", "📝"),
    ("Config", "⚙"),
    ("Analytics", "📊"),
]


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Morphix")
        self.setFixedSize(400, 220)
        self.setStyleSheet(f"background-color: {COLORS['bg_deepest']};")
        self._logged_in = False

        title = QLabel("Morphix")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size: 28px; font-weight: bold; color: {ACCENT};")

        subtitle = QLabel("Sistema de Razonamiento Avanzado")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"font-size: 13px; color: {COLORS['text_secondary']};")

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Contraseña maestra")
        self.password.setStyleSheet(
            f"QLineEdit {{ background: {COLORS['bg_surface']}; color: {COLORS['text_primary']}; "
            f"border: 1px solid {ACCENT}; "
            f"border-radius: 6px; padding: 8px; font-size: 14px; }}"
        )
        self.password.returnPressed.connect(self._login)

        self.login_btn = QPushButton("Iniciar Sesión")
        self.login_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: {COLORS['bg_deepest']}; "
            f"border-radius: 8px; padding: 10px; font-size: 14px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_hover']}; }}"
        )
        self.login_btn.clicked.connect(self._login)

        self.error = QLabel("")
        self.error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error.setStyleSheet(f"color: {COLORS['error']}; font-size: 12px;")
        self.error.setVisible(False)

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

            if bcrypt.checkpw(password.encode(), settings.password_hash.encode()):
                self._logged_in = True
                self.accept()
            else:
                self._show_error("Contraseña incorrecta")
                self._reset_login_ui()
        except Exception as e:
            logger.error(f"Error en login: {e}", exc_info=True)
            self._show_error(f"Error: {e!s}")
            self._reset_login_ui()

    def _reset_login_ui(self):
        self.login_btn.setEnabled(True)
        self.login_btn.setText("Iniciar Sesión")
        self.password.setEnabled(True)
        self.progress.hide()

    def _show_error(self, msg: str):
        self.error.setText(msg)
        self.error.setVisible(True)
        QTimer.singleShot(5000, self.error.hide)

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in


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
            f"font-size: 18px; font-weight: bold; color: {ACCENT}; " f"padding: 16px 8px 12px 8px;"
        )
        sidebar_layout.addWidget(logo)

        self._sidebar = QListWidget()
        self._sidebar.setStyleSheet(StyleFactory.sidebar())
        for label, icon in _SIDEBAR_ITEMS:
            item = QListWidgetItem(f"  {icon}  {label}")
            item.setToolTip(f"Ver {label}")
            self._sidebar.addItem(item)
        self._sidebar.setCurrentRow(0)
        sidebar_layout.addWidget(self._sidebar, 1)

        # Workspace indicator at sidebar bottom
        self._sidebar_ws_label = QLabel("ws: main")
        self._sidebar_ws_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; " f"padding: 8px 16px 12px 16px;"
        )
        sidebar_layout.addWidget(self._sidebar_ws_label)
        layout.addWidget(sidebar_widget)

        # Content (stacked widget)
        self._stacked = QStackedWidget()
        self.tabs = _StackedShim(self._stacked)

        loading = QLabel("Inicializando...")
        loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading.setStyleSheet("color: #A0A0A0; font-size: 16px;")
        self._loading_label = loading
        self.tabs.addTab(loading, "Maestro")
        self._stacked.setCurrentIndex(0)
        layout.addWidget(self._stacked, 1)

        # Sidebar navigation
        self._sidebar.currentRowChanged.connect(self._stacked.setCurrentIndex)
        self.setCentralWidget(central)

    def _apply_dark_theme(self):
        from core.config import settings

        if not settings.dark_mode:
            return
        palette = self.palette()
        for role, color in get_dark_palette().items():
            palette.setColor(QPalette.ColorGroup.All, role, QColor(color))
        self.setPalette(palette)
        self.setStyleSheet(
            StyleFactory.tab_widget() + StyleFactory.menu_bar() + StyleFactory.status_bar()
        )

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
        self.workspace_label = QLabel("Workspace: main")
        self.workspace_label.setStyleSheet("color: #A0A0A0;")
        self.status.addPermanentWidget(self.workspace_label)
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
        editor = EditorTab()
        self.editor = editor
        self.tabs.addTab(editor, "Editor")  # index 3
        self.tabs.addTab(ConfigTab(), "Config")  # index 4
        self.tabs.addTab(AnalyticsTab(), "Analytics")  # index 5

        # Sync sidebar → content
        self._sidebar.setCurrentRow(0)

        from core.workspaces import get_global_workspaces

        ws_name = get_global_workspaces().current
        self.workspace_label.setText(f"Workspace: {ws_name}")
        if hasattr(self, "_sidebar_ws_label"):
            self._sidebar_ws_label.setText(f"ws: {ws_name}")
        self.status.showMessage("✅ Morphix listo", 5000)

        from desktop.events import get_signals

        get_signals().offline_changed.connect(
            lambda offline: self.status.showMessage(
                f"⚠️ Modo offline {'activado' if offline else 'desactivado'}", 8000
            )
        )

        def _on_ws_change(ws):
            self.workspace_label.setText(f"Workspace: {ws}")
            if hasattr(self, "_sidebar_ws_label"):
                self._sidebar_ws_label.setText(f"ws: {ws}")

        get_signals().workspace_changed.connect(_on_ws_change)
        get_signals().project_changed.connect(
            lambda root: self.editor.set_project(root or None, get_global_workspaces().current)
        )
        editor.set_project(maestro._current_project_root, get_global_workspaces().current)

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

    def setTabPosition(self, _pos) -> None:
        pass  # no-op for backward compat

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
