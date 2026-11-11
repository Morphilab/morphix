"""Diálogo de aprobación de skills locales.

Se abre al cambiar de workspace cuando hay skills locales pendientes de
aprobación o cuyo contenido cambió desde la última aprobación. Aprobar
guarda el hash del body en el sidecar; rechazar marca rechazo explícito.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
)

from desktop.theme import COLORS, StyleFactory

logger = logging.getLogger(__name__)


class SkillsApprovalDialog(QDialog):
    def __init__(self, workspace: str, pendings: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self._workspace = workspace
        self._pendings: list[tuple[str, str, str]] = list(pendings)
        self.setWindowTitle(f"Skills locales — {workspace}")
        self.setMinimumSize(560, 420)
        self._build_ui()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(16, 14, 16, 14)
        main.setSpacing(10)

        intro = QLabel(
            "Estas skills LOCALES del workspace esperan revisión. Aprueba las que "
            "quieras que los agentes puedan cargar; rechaza las que no.\n"
            "Las skills no aprobadas NO se inyectan a los agentes."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        main.addWidget(intro)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.skills_list = QListWidget()
        self.skills_list.currentRowChanged.connect(self._on_selected)
        splitter.addWidget(self.skills_list)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet(
            f"QPlainTextEdit {{ background: {COLORS['bg_surface']};"
            f" color: {COLORS['text_primary']}; border: 1px solid"
            f" {COLORS['border_default']}; border-radius: 6px;"
            " font-size: 12px; font-family: monospace; }"
        )
        splitter.addWidget(self.preview)
        splitter.setSizes([220, 340])
        main.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.approve_btn = QPushButton("✅ Aprobar")
        self.approve_btn.setStyleSheet(StyleFactory.success_button())
        self.approve_btn.clicked.connect(self._approve_current)
        buttons.addWidget(self.approve_btn)
        self.reject_btn = QPushButton("🚫 Rechazar")
        self.reject_btn.setStyleSheet(StyleFactory.secondary_button())
        self.reject_btn.clicked.connect(self._reject_current)
        buttons.addWidget(self.reject_btn)
        buttons.addStretch()
        approve_all_btn = QPushButton("Aprobar todo")
        approve_all_btn.setStyleSheet(StyleFactory.secondary_button())
        approve_all_btn.clicked.connect(self._approve_all)
        buttons.addWidget(approve_all_btn)
        close_btn = QPushButton("Cerrar")
        close_btn.setStyleSheet(StyleFactory.secondary_button())
        close_btn.clicked.connect(self._close_without_deciding)
        buttons.addWidget(close_btn)
        main.addLayout(buttons)

        self._reload()

    def _reload(self):
        self.skills_list.blockSignals(True)
        self.skills_list.clear()
        for name, status, _body in self._pendings:
            badge = "nueva" if status == "pending" else "modificada"
            item = QListWidgetItem(f"{name}  ({badge})")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.skills_list.addItem(item)
        self.skills_list.blockSignals(False)
        if self._pendings:
            self.skills_list.setCurrentRow(0)
        else:
            self.accept()

    def _current(self) -> tuple[str, str, str] | None:
        row = self.skills_list.currentRow()
        return self._pendings[row] if 0 <= row < len(self._pendings) else None

    def _on_selected(self, _row: int):
        cur = self._current()
        self.preview.setPlainText(cur[2] if cur else "")

    def _approve_current(self):
        cur = self._current()
        if cur is None:
            return
        from core.skills_approval import approve_skill

        approve_skill(self._workspace, cur[0], cur[2])
        self._consume(cur[0])

    def _reject_current(self):
        cur = self._current()
        if cur is None:
            return
        from core.skills_approval import reject_skill

        reject_skill(self._workspace, cur[0])
        self._consume(cur[0])

    def _approve_all(self):
        from core.skills_approval import approve_skill

        for name, _status, body in list(self._pendings):
            approve_skill(self._workspace, name, body)
        self._pendings.clear()
        self._reload()

    def _consume(self, name: str):
        self._pendings = [p for p in self._pendings if p[0] != name]
        self._reload()

    def _close_without_deciding(self):
        """Cerrar sin decidir: las skills quedan pending y siguen ocultas."""
        QMessageBox.information(
            self,
            "Skills sin aprobar",
            "Las skills sin decisión quedan ocultas para los agentes; este diálogo "
            "reaparecerá en el próximo cambio de workspace.",
        )
        self.accept()
