# mypy: ignore-errors
"""Chat panel — scroll area, message bubbles, PDF input, send input row."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import INPUT_STYLE, StyleFactory

if TYPE_CHECKING:
    from desktop.maestro_tab import MaestroTab


def build_chat_panel(tab: MaestroTab) -> QWidget:
    # --- Center panel: Chat ---
    center = QWidget()
    center_layout = QVBoxLayout(center)
    center_layout.setContentsMargins(4, 4, 4, 8)
    center_layout.setSpacing(8)

    tab.chat_scroll = QScrollArea()
    tab.chat_scroll.setWidgetResizable(True)
    tab.chat_scroll.setStyleSheet(StyleFactory.scroll_area_chat())
    tab.chat_container = QWidget()
    tab.chat_layout = QVBoxLayout(tab.chat_container)
    tab.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    tab.chat_layout.setSpacing(4)
    tab.chat_layout.addStretch()
    tab.chat_scroll.setWidget(tab.chat_container)
    tab.chat_scroll.viewport().installEventFilter(tab)

    # Multiline input (Ctrl+Enter = send, Shift+Enter = newline)
    tab.input_field = QTextEdit()
    tab.input_field.setPlaceholderText("¿Qué quieres que coordine el Maestro?")
    tab.input_field.setMaximumHeight(100)
    tab.input_field.setAcceptRichText(False)
    tab.input_field.setStyleSheet(INPUT_STYLE)
    tab.input_field.installEventFilter(tab)

    # PDF input row
    tab.pdf_path_field = QLineEdit()
    tab.pdf_path_field.setPlaceholderText("Ruta de PDF (opcional)")
    tab.pdf_path_field.setStyleSheet(StyleFactory.input_line())
    tab.pdf_load_btn = QPushButton("Cargar")
    tab.pdf_load_btn.setStyleSheet(StyleFactory.secondary_button())
    tab.pdf_load_btn.clicked.connect(tab._load_pdf)
    tab._current_pdf_text = ""

    pdf_row = QHBoxLayout()
    pdf_row.addWidget(tab.pdf_path_field, 1)
    pdf_row.addWidget(tab.pdf_load_btn)

    tab.send_btn = QPushButton("Enviar")
    tab.send_btn.setStyleSheet(StyleFactory.accent_button())
    tab.send_btn.clicked.connect(tab.send_message)

    input_row = QHBoxLayout()
    input_row.addWidget(tab.input_field, 1)
    input_row.addWidget(tab.send_btn)

    center_layout.addWidget(tab.chat_scroll, 1)
    center_layout.addLayout(pdf_row)
    center_layout.addLayout(input_row)
    return center
