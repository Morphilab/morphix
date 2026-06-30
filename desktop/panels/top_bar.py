# mypy: ignore-errors
"""Maestro top bar — mode indicator, workspace, project/agent, preload, actions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config import settings
from desktop.theme import COLORS, StyleFactory

if TYPE_CHECKING:
    from desktop.maestro_tab import MaestroTab


def build_top_bar(tab: MaestroTab) -> QWidget:
    bar = QWidget()
    bar.setStyleSheet(f"QWidget {{ background: {COLORS['bg_near_black']}; }}")
    outer = QVBoxLayout(bar)
    outer.setContentsMargins(8, 6, 8, 6)
    outer.setSpacing(6)

    proj_btn_style = StyleFactory.small_button()
    combo_style = StyleFactory.combo_box()
    tab._toggle_style_active = StyleFactory.toggle_active()
    tab._toggle_style_inactive = StyleFactory.toggle_inactive()

    # --- Row 1: mode  project  agent ---
    row1 = QHBoxLayout()
    row1.setSpacing(6)
    is_off = settings.offline_mode
    tab.mode_label = QLabel("Offline" if is_off else "Online")
    tab.mode_label.setStyleSheet(
        f"color: {'#F59E0B' if is_off else '#22C55E'}; font-size: 11px; font-weight: bold;"
    )

    from core.workspaces import get_global_workspaces

    tab.ws_label = QLabel(get_global_workspaces().current)
    tab.ws_label.setStyleSheet("color: #A0A0A0; font-size: 11px;")
    row1.addWidget(tab.mode_label)
    row1.addWidget(QLabel("·"))
    row1.addWidget(QLabel("ws:"))
    row1.addWidget(tab.ws_label)

    tab._chat_toggle = QPushButton("💬 Chat")
    tab._chat_toggle.setStyleSheet(tab._toggle_style_active)
    tab._orchestrate_toggle = QPushButton("⚙️ Orquestar")
    tab._orchestrate_toggle.setStyleSheet(tab._toggle_style_inactive)
    tab._chat_toggle.clicked.connect(lambda: tab._set_mode("chat"))
    tab._orchestrate_toggle.clicked.connect(lambda: tab._set_mode("orchestrate"))
    row1.addSpacing(12)
    row1.addWidget(QLabel("Modo:"))
    row1.addWidget(tab._chat_toggle)
    row1.addWidget(tab._orchestrate_toggle)

    tab._project_label = QLabel("Proyecto: —")
    tab._project_label.setStyleSheet("color: #A0A0A0; font-size: 10px; padding: 2px 4px;")
    tab._project_combo = QComboBox()
    tab._project_combo.setStyleSheet(combo_style)
    tab._refresh_project_list()
    tab._project_combo.currentIndexChanged.connect(tab._on_project_combo_changed)
    tab._new_proj_btn = QPushButton("➕ Nuevo")
    tab._new_proj_btn.setStyleSheet(proj_btn_style)
    tab._new_proj_btn.clicked.connect(tab._create_project)
    tab._import_proj_btn = QPushButton("📂 Importar")
    tab._import_proj_btn.setStyleSheet(proj_btn_style)
    tab._import_proj_btn.clicked.connect(tab._import_project)
    row1.addSpacing(12)
    row1.addWidget(tab._project_label)
    row1.addWidget(tab._project_combo)
    row1.addWidget(tab._new_proj_btn)
    row1.addWidget(tab._import_proj_btn)

    tab._agent_combo = QComboBox()
    tab._agent_combo.setStyleSheet(combo_style)
    tab._agent_combo.setMinimumWidth(130)
    tab._populate_agents(None)
    tab._agent_combo.currentIndexChanged.connect(tab._on_agent_combo_changed)
    row1.addSpacing(12)
    row1.addWidget(QLabel("Agente:"))
    row1.addWidget(tab._agent_combo)
    row1.addStretch()
    outer.addLayout(row1)

    # --- Row 2: preload  conversation actions ---
    row2 = QHBoxLayout()
    row2.setSpacing(6)
    tab._preload_btn = QPushButton("⚡ Pre-cargar proyecto")
    tab._preload_btn.setStyleSheet(proj_btn_style)
    tab._preload_btn.clicked.connect(tab._preload_project)
    tab._preload_btn.setEnabled(False)
    tab._preload_status = QLabel("")
    tab._preload_status.setStyleSheet("color: #A0A0A0; font-size: 10px;")
    tab._preload_progress = QProgressBar()
    tab._preload_progress.setRange(0, 100)
    tab._preload_progress.setValue(0)
    tab._preload_progress.setMaximumHeight(14)
    tab._preload_progress.setMaximumWidth(160)
    tab._preload_progress.setTextVisible(False)
    tab._preload_progress.setVisible(False)
    tab._preload_progress.setStyleSheet(StyleFactory.progress_bar(COLORS["success"]))
    row2.addWidget(tab._preload_btn)
    row2.addWidget(tab._preload_progress)
    row2.addWidget(tab._preload_status, 1)
    row2.addStretch()

    btn_style = StyleFactory.secondary_button()
    tab.offline_btn = QPushButton("Desactivar Offline" if is_off else "Activar Offline")
    tab.offline_btn.setStyleSheet(btn_style)
    tab.offline_btn.clicked.connect(tab._toggle_offline)
    tab.clear_btn = QPushButton("Limpiar")
    tab.clear_btn.setStyleSheet(btn_style)
    tab.clear_btn.clicked.connect(tab.clear_chat)
    tab.download_btn = QPushButton("Descargar")
    tab.download_btn.setStyleSheet(btn_style)
    tab.download_btn.clicked.connect(tab._download_conversation)
    tab.download_format = QComboBox()
    tab.download_format.addItems(["md", "json", "pdf", "html"])
    tab.download_format.setStyleSheet(combo_style)
    tab._new_conv_btn = QPushButton("✚ Nueva conversación")
    tab._new_conv_btn.setStyleSheet(btn_style)
    tab._new_conv_btn.clicked.connect(tab._new_conversation)
    row2.addWidget(tab.clear_btn)
    row2.addWidget(tab.download_btn)
    row2.addWidget(tab.download_format)
    row2.addWidget(tab._new_conv_btn)
    row2.addWidget(tab.offline_btn)
    outer.addLayout(row2)
    return bar
