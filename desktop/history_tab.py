"""History Tab — lista de conversaciones y detalle."""

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.sanitize import sanitize_rich_text
from desktop.theme import COLORS, StyleFactory

logger = logging.getLogger(__name__)

from desktop.async_helpers import run_async


class HistoryTab(QWidget):
    conversation_selected = Signal(int)
    # una conversación eliminada debe invalidar el `_conversation_id`
    # de cualquier pane de Maestro que la tenga cargada (si no, el próximo
    # mensaje fallaría el save en silencio).
    conversation_deleted = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_id = None
        self._pending_search: str = ""
        self._build_ui()
        run_async(self._load_list())

    def _build_ui(self):
        # Lista de conversaciones
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        self.conv_list = QListWidget()
        self.conv_list.setStyleSheet(StyleFactory.list_widget())
        self.conv_list.itemClicked.connect(lambda item: run_async(self._on_select(item)))

        refresh_btn = QPushButton("Refrescar")
        refresh_btn.setStyleSheet(StyleFactory.primary_button())
        refresh_btn.clicked.connect(lambda: run_async(self._load_list()))

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("⌕ Buscar (texto · date:YYYY-MM-DD · tag:x)")
        self.search_field.setStyleSheet(StyleFactory.input_line())
        self.search_field.setClearButtonEnabled(True)
        self.search_field.returnPressed.connect(
            lambda: run_async(self._search_now())
        )  # Enter = inmediato
        self.search_field.textChanged.connect(self._queue_search)
        # Debounce: no golpear la BD en cada tecla (300ms tras la última).
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(lambda: run_async(self._search_now()))

        left_layout.addWidget(QLabel("Conversaciones"))
        left_layout.addWidget(self.search_field)
        left_layout.addWidget(self.conv_list)
        left_layout.addWidget(refresh_btn)

        # Detalle
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)

        self.detail_view = QTextBrowser()
        self.detail_view.setOpenExternalLinks(True)
        self.detail_view.setStyleSheet(StyleFactory.text_browser())
        self.detail_view.setPlaceholderText("Selecciona una conversación para ver su contenido")

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            f"color: {COLORS['text_dim']}; font-size: 11px; padding: 2px 4px;"
        )
        self.status_label.setWordWrap(True)

        actions = QHBoxLayout()
        self.export_btn = QPushButton("Exportar")
        self.export_btn.setStyleSheet(StyleFactory.primary_button())
        self.export_btn.clicked.connect(lambda: run_async(self._export()))

        self.delete_btn = QPushButton("Eliminar")
        self.delete_btn.setStyleSheet(StyleFactory.danger_button())
        self.delete_btn.clicked.connect(lambda: run_async(self._delete()))

        self.format_combo = QComboBox()
        self.format_combo.addItems(["md", "json", "pdf"])
        self.format_combo.setStyleSheet(StyleFactory.combo_box())

        self.resume_btn = QPushButton("Continuar")
        self.resume_btn.setStyleSheet(StyleFactory.success_button())
        self.resume_btn.clicked.connect(self._on_resume_clicked)

        actions.addWidget(self.export_btn)
        actions.addWidget(self.format_combo)
        actions.addWidget(self.delete_btn)
        actions.addWidget(self.resume_btn)
        actions.addStretch()

        right_layout.addWidget(self.detail_view)
        right_layout.addWidget(self.status_label)
        right_layout.addLayout(actions)

        left.setMinimumWidth(200)
        right.setMinimumWidth(300)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.addWidget(splitter)
        self._selected_id = None

    def _show_status(self, msg: str):
        self.status_label.setText(msg)
        QTimer.singleShot(6000, lambda: self.status_label.clear())

    async def _load_list(self, query: str = ""):
        try:
            from desktop.services.history_service import HistoryService

            self.conv_list.clear()
            conversations = await HistoryService.list_conversations(query)
            for conv in conversations:
                item = QListWidgetItem(f"[{conv['id']}] {conv['title']}")
                item.setData(Qt.ItemDataRole.UserRole, conv["id"])
                self.conv_list.addItem(item)
        except Exception as e:
            logger.exception("Error cargando lista de conversaciones")
            self._show_status(f"Error al cargar: {e}")

    # ── Buscador ──

    def _queue_search(self, text: str) -> None:
        """Debounce 300ms: cada tecla re-arma el timer."""
        self._pending_search = text
        self._search_timer.start()

    async def _search_now(self) -> None:
        self._search_timer.stop()
        query = self._pending_search
        self._pending_search = ""
        await self._load_list(query)

    async def _on_select(self, item: QListWidgetItem):
        conv_id = item.data(Qt.ItemDataRole.UserRole)
        if not conv_id:
            return
        self._selected_id = conv_id
        try:
            from desktop.services.history_service import HistoryService

            messages = await HistoryService.get_messages(conv_id)
            text = f"# Conversación #{conv_id}\n\n"
            for m in messages:
                role = m["role"].upper()
                content = m["content"]
                text += f"**{role}:** {content}\n\n---\n\n"
            self.detail_view.setMarkdown(sanitize_rich_text(text))
        except Exception as e:
            logger.exception("Error cargando detalle de conversación")
            self._show_status(f"Error: {e}")

    async def _export(self):
        if not self._selected_id:
            return
        try:
            from desktop.services.history_service import HistoryService

            fmt = self.format_combo.currentText()
            filename = await HistoryService.export_conversation(self._selected_id, fmt)
            self._show_status(f"✅ Exportado a {filename}")
        except Exception as e:
            logger.exception("Error exportando conversación")
            self._show_status(f"Error: {e}")

    async def _delete(self):
        if not self._selected_id:
            return
        try:
            from desktop.services.history_service import HistoryService

            await HistoryService.delete_conversation(self._selected_id)
            self.conversation_deleted.emit(self._selected_id)  # invalidar panes
            self._selected_id = None
            self.detail_view.clear()
            self._show_status("Conversación eliminada")
            await self._load_list()
        except Exception as e:
            logger.exception("Error eliminando conversación")
            self._show_status(f"Error: {e}")

    def _on_resume_clicked(self):
        if self._selected_id:
            self.conversation_selected.emit(self._selected_id)
