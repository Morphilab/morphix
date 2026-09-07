# mypy: ignore-errors
"""Chat panel — scroll area, message bubbles, PDF input, send input row."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop.icons import get_icon
from desktop.theme import COLORS, INPUT_STYLE, StyleFactory

if TYPE_CHECKING:
    from desktop.maestro_tab import SessionPane


def build_chat_panel(tab: SessionPane) -> QWidget:
    # -- Center panel: Chat ---
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
    tab.chat_container.setAutoFillBackground(False)
    tab.chat_scroll.viewport().installEventFilter(tab)

    # Nota de sesión vacía: guía la primera
    # acción con formato de NOTA (borde dashed, italic, dim) — distinto de un
    # mensaje. Vive SOLO mientras el chat está vacío: se oculta con el primer
    # contenido (burbuja/typing/debate) y reaparece en clear_chat.
    tab._idle_note = QLabel(
        "◌  Lanza un workflow desde el Dashboard o escribe tu tarea abajo — "
        "el progreso, las subtareas y los archivos aparecerán en el panel de actividad."
    )
    tab._idle_note.setWordWrap(True)
    tab._idle_note.setStyleSheet(
        f"color: {COLORS['text_dim']}; font-size: 11px; font-style: italic; "
        f"border: 1px dashed {COLORS['border_light']}; border-radius: 9px; "
        f"padding: 10px 12px; background: transparent;"
    )
    tab._idle_note.setVisible(True)
    tab.chat_layout.insertWidget(tab.chat_layout.count() - 1, tab._idle_note)

    # Multiline input (Ctrl+Enter = send, Shift+Enter = newline)
    tab.input_field = QTextEdit()
    tab.input_field.setPlaceholderText("¿Qué quieres que coordine el Maestro?")
    tab.input_field.setMaximumHeight(100)
    tab.input_field.setAcceptRichText(False)
    tab.input_field.setStyleSheet(INPUT_STYLE)
    tab.input_field.installEventFilter(tab)

    # Adjunto PDF — fuera del flujo permanente; el widget se conserva oculto
    # porque _on_pdf_loaded lee la ruta desde él (compat interna).
    tab.pdf_path_field = QLineEdit()
    tab.pdf_path_field.setVisible(False)
    tab._current_pdf_text = ""

    tab._attach_btn = QPushButton()
    tab._attach_btn.setIcon(get_icon("attach", COLORS["text_secondary"]))
    tab._attach_btn.setToolTip("Adjuntar PDF (opcional)")
    tab._attach_btn.setStyleSheet(StyleFactory.ghost_button())
    tab._attach_btn.clicked.connect(tab._attach_pdf)

    tab.send_btn = QPushButton("Enviar")
    tab.send_btn.setStyleSheet(StyleFactory.accent_button())
    tab.send_btn.clicked.connect(tab.send_message)

    # botón de parada por sesión (⏹). Visible solo mientras
    # esta sesión tiene un workflow en curso.
    tab._stop_btn = QPushButton("⏹")
    tab._stop_btn.setToolTip("Detener la ejecución de esta sesión")
    tab._stop_btn.setStyleSheet(StyleFactory.danger_button())
    tab._stop_btn.setVisible(False)
    tab._stop_btn.clicked.connect(tab._stop_workflow)

    input_row = QHBoxLayout()
    input_row.addWidget(tab._attach_btn)
    input_row.addWidget(tab.input_field, 1)
    input_row.addWidget(tab.send_btn)
    input_row.addWidget(tab._stop_btn)

    center_layout.addWidget(tab.chat_scroll, 1)

    # Status banner (errores/pausa de clarificación) — oculto por defecto
    tab._status_banner = QLabel("")
    tab._status_banner.setWordWrap(True)
    tab._status_banner.setVisible(False)
    tab._status_banner.setStyleSheet(StyleFactory.status_banner("info"))
    center_layout.addWidget(tab._status_banner)

    # abandono EXPLÍCITO de la pausa de clarificación — visible solo
    # mientras haya una pausa armada (ver SessionPane._update_pause_affordance).
    tab._abandon_pause_btn = QPushButton("⏸ Abandonar pausa")
    tab._abandon_pause_btn.setStyleSheet(StyleFactory.ghost_button())
    tab._abandon_pause_btn.setToolTip(
        "Descarta la pausa de clarificación en esta sesión "
        "(la fila persiste en BD y se recupera recargando la conversación)"
    )
    tab._abandon_pause_btn.setVisible(False)
    tab._abandon_pause_btn.clicked.connect(tab._abandon_pause)
    center_layout.addWidget(tab._abandon_pause_btn)

    center_layout.addLayout(input_row)
    return center
