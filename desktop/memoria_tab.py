"""Memoria Tab — inspección y borrado de la memoria del workspace.

Superficie GUI sobre memory_inspector: lista de claves → detalle → borrado.
El borrado pide confirmación humana y SIEMPRE viaja con confirm_delete=True
(la política vive en desktop, no en core).
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from desktop.async_helpers import run_async
from desktop.services.memoria_service import delete_key, list_keys, read_key
from desktop.theme import COLORS, StyleFactory

logger = logging.getLogger(__name__)


class MemoriaTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        run_async(self.refresh())
        # La memoria es por-workspace: sin esto el tab quedaba stale tras un
        # switch (mostraba claves del workspace anterior). Patrón bots_tab.
        from desktop.events import get_signals

        get_signals().workspace_changed.connect(self._on_workspace_changed)

    def _on_workspace_changed(self, _ws: str) -> None:
        run_async(self.refresh())

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(20, 16, 20, 16)
        main.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Memoria del workspace")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {COLORS['text_primary']};")
        header.addWidget(title)
        header.addStretch()
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        header.addWidget(self.status_label)
        refresh_btn = QPushButton("⟳ Refrescar")
        refresh_btn.setStyleSheet(StyleFactory.secondary_button())
        refresh_btn.clicked.connect(lambda: run_async(self.refresh()))
        header.addWidget(refresh_btn)
        main.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.keys_list = QListWidget()
        self.keys_list.setStyleSheet(
            f"QListWidget {{ background: {COLORS['bg_surface']}; color: {COLORS['text_primary']};"
            f" border: 1px solid {COLORS['border_default']}; border-radius: 6px; font-size: 13px; }}"
        )
        self.keys_list.currentRowChanged.connect(self._on_selected)
        splitter.addWidget(self.keys_list)

        self.detail_view = QPlainTextEdit()
        self.detail_view.setReadOnly(True)
        self.detail_view.setStyleSheet(
            f"QPlainTextEdit {{ background: {COLORS['bg_surface']}; color: {COLORS['text_primary']};"
            f" border: 1px solid {COLORS['border_default']}; border-radius: 6px; font-size: 12px;"
            " font-family: monospace; }"
        )
        splitter.addWidget(self.detail_view)
        splitter.setSizes([260, 480])
        main.addWidget(splitter, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        self.delete_btn = QPushButton("🗑 Eliminar")
        self.delete_btn.setStyleSheet(StyleFactory.secondary_button())
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._on_delete)
        footer.addWidget(self.delete_btn)
        main.addLayout(footer)

    async def refresh(self):
        keys = await list_keys()
        self.keys_list.blockSignals(True)
        self.keys_list.clear()
        for entry in keys:
            item = QListWidgetItem(f"{entry['key']} · {entry['chars']:,} chars")
            item.setData(Qt.ItemDataRole.UserRole, entry["key"])
            self.keys_list.addItem(item)
        self.keys_list.blockSignals(False)
        self.detail_view.setPlainText("")
        self.delete_btn.setEnabled(False)
        self.status_label.setText(f"{len(keys)} claves")

    def _current_key(self) -> str | None:
        item = self.keys_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _on_selected(self, _row: int):
        key = self._current_key()
        if key is None:
            self.detail_view.setPlainText("")
            self.delete_btn.setEnabled(False)
            return
        self.delete_btn.setEnabled(True)

        async def _load():
            value = await read_key(key)
            if self._current_key() == key:
                self.detail_view.setPlainText(value or "(vacía)")

        run_async(_load())

    def _on_delete(self):
        key = self._current_key()
        if key is None:
            return
        answer = QMessageBox.question(
            self,
            "Eliminar clave de memoria",
            f"¿Eliminar '{key}' de la memoria del workspace?\n\nEsta acción no se puede deshacer.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        async def _do_delete():
            ok = await delete_key(key)
            self.status_label.setText("🗑 eliminada" if ok else "error al eliminar")
            await self.refresh()

        run_async(_do_delete())
