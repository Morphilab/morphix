"""History Tab — lista de conversaciones y detalle."""

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import ACCENT, StyleFactory

logger = logging.getLogger(__name__)

from desktop.async_helpers import run_async


class HistoryTab(QWidget):
    conversation_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
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
        refresh_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: #FFF; border-radius: 6px; padding: 6px; }}"
        )
        refresh_btn.clicked.connect(lambda: run_async(self._load_list()))

        left_layout.addWidget(QLabel("Conversaciones"))
        left_layout.addWidget(self.conv_list)
        left_layout.addWidget(refresh_btn)

        # Detalle
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)

        self.detail_view = QTextBrowser()
        self.detail_view.setOpenExternalLinks(True)
        self.detail_view.setStyleSheet(
            "QTextBrowser { background: #1A1A1A; color: #E5E5E5; border: 1px solid #2A2A2A; "
            "border-radius: 8px; padding: 8px; font-size: 13px; }"
        )
        self.detail_view.setPlaceholderText("Selecciona una conversación para ver su contenido")

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 11px; padding: 2px 4px;")
        self.status_label.setWordWrap(True)

        actions = QHBoxLayout()
        self.export_btn = QPushButton("Exportar")
        self.export_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: #FFF; border-radius: 6px; padding: 6px; }}"
        )
        self.export_btn.clicked.connect(lambda: run_async(self._export()))

        self.delete_btn = QPushButton("Eliminar")
        self.delete_btn.setStyleSheet(StyleFactory.danger_button())
        self.delete_btn.clicked.connect(lambda: run_async(self._delete()))

        self.format_combo = QComboBox()
        self.format_combo.addItems(["md", "json", "pdf"])
        self.format_combo.setStyleSheet(
            "QComboBox { background: #1A1A1A; color: #E5E5E5; border: 1px solid #2A2A2A; "
            "border-radius: 6px; padding: 4px; font-size: 12px; }"
        )

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

    async def _load_list(self):
        try:
            from desktop.services.history_service import HistoryService

            self.conv_list.clear()
            conversations = await HistoryService.list_conversations()
            for conv in conversations:
                item = QListWidgetItem(f"[{conv['id']}] {conv['title']}")
                item.setData(Qt.ItemDataRole.UserRole, conv["id"])
                self.conv_list.addItem(item)
        except Exception as e:
            logger.exception("Error cargando lista de conversaciones")
            self._show_status(f"Error al cargar: {e}")

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
            self.detail_view.setMarkdown(text)
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
