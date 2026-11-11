# mypy: ignore-errors
"""Maestro top bar — 1 fila compacta: logo · modo · workflow · agente · proyecto · ＋ · export."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import COLORS, StyleFactory

if TYPE_CHECKING:
    from desktop.maestro_tab import SessionPane


def build_top_bar(tab: SessionPane) -> QWidget:
    """1 fila compacta: logo · modo · workflow · agente · proyecto · ＋ · export.

    spec §5: 6 visibles. Sin duplicados (Online/ws viven en el sidebar).
    """
    bar = QWidget()
    # Regla de 2 tonos, variante estricta): chrome/canvas =
    # bg_deepest; superficies interactivas = bg_solid. Solo hover se aparta.
    bar.setStyleSheet(f"QWidget {{ background: {COLORS['bg_deepest']}; }}")
    outer = QVBoxLayout(bar)
    outer.setContentsMargins(8, 4, 8, 4)
    outer.setSpacing(6)

    combo_style = StyleFactory.combo_box()
    tag_style = StyleFactory.small_button()

    row = QHBoxLayout()
    row.setSpacing(4)

    # Logo "Morphix" eliminado: era un QLabel por cada SessionPane
    # (hasta 4× con multi-sesión) y el sidebar
    # ya lleva la marca. La fila gana ~60px para los combos.

    # ── Modo (estado — NUNCA en menú, spec §2) ──
    # Segmentado real (aprobado 2-A): el contenedor NO tiene borde propio;
    # el activo lleva fondo claro y radio solo en su extremo exterior —
    # un solo bloque, sin bordes intermedios ni esquinas que chocan.
    tab._chat_toggle = QPushButton("Chat")
    tab._chat_toggle.setToolTip("Modo conversación directa")
    tab._orchestrate_toggle = QPushButton("Orquestar")
    tab._orchestrate_toggle.setToolTip("Modo orquestación de workflows")
    tab._chat_toggle.clicked.connect(lambda: tab._set_mode("chat"))
    tab._orchestrate_toggle.clicked.connect(lambda: tab._set_mode("orchestrate"))
    mode_grp = QWidget()
    mode_lay = QHBoxLayout(mode_grp)
    mode_lay.setContentsMargins(0, 0, 0, 0)
    mode_lay.setSpacing(0)
    mode_lay.addWidget(tab._chat_toggle)
    mode_lay.addWidget(tab._orchestrate_toggle)
    tab._apply_mode_styles()
    row.addWidget(mode_grp)

    # ── Picker de workflow (NUEVO — solo visible en Orquestar) ──
    tab._workflow_combo = QComboBox()
    tab._workflow_combo.setStyleSheet(combo_style)
    tab._workflow_combo.setToolTip("Workflow activo (Orquestar)")
    tab._workflow_combo.currentTextChanged.connect(tab._on_workflow_picked)
    tab._workflow_combo.setVisible(False)
    row.addWidget(tab._workflow_combo)

    # ── Agente ──
    tab._agent_combo = QComboBox()
    tab._agent_combo.setStyleSheet(combo_style)
    tab._agent_combo.setMinimumWidth(100)
    tab._agent_combo.setToolTip("Agente activo")
    tab._populate_agents(None)
    tab._agent_combo.currentIndexChanged.connect(tab._on_agent_combo_changed)
    tab._agent_label = QLabel("Agente:")
    row.addWidget(tab._agent_label)
    row.addWidget(tab._agent_combo)

    # ── Info contextual ──
    # Un solo botón cuyo texto cambia según el contexto: ⓘ Agente (chat),
    # ⓘ Workflow (orquestar), ⓘ Bot (chat eterno de bot). El label lo
    # recalcula SessionPane._update_info_button(); el contenido lo resuelve
    # SessionPane._open_info_dialog() → EntityInfoDialog.
    tab._info_btn = QPushButton("ⓘ Agente")
    tab._info_btn.setStyleSheet(tag_style)
    tab._info_btn.setToolTip("Información de la entidad activa")
    tab._info_btn.clicked.connect(tab._open_info_dialog)
    row.addWidget(tab._info_btn)

    # ── Indicador de proyecto con menú (spec §5) ──
    tab._project_btn = QToolButton()
    tab._project_btn.setText("▤ — sin proyecto")
    tab._project_btn.setStyleSheet(project_button_style(False))
    tab._project_menu = QMenu(tab._project_btn)
    tab._preload_action = tab._project_menu.addAction("⟳  Pre-cargar índice")
    tab._preload_action.setEnabled(False)
    tab._project_menu.addSeparator()
    tab._change_project_action = tab._project_menu.addAction("▤  Cambiar proyecto → Dashboard")
    tab._preload_action.triggered.connect(tab._preload_project)
    tab._change_project_action.triggered.connect(tab._request_change_project)
    tab._project_btn.setMenu(tab._project_menu)
    tab._project_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    tab._project_btn.setToolTip(
        "Proyecto activo del workspace (se cambia desde el Dashboard → Proyectos)"
    )
    row.addWidget(tab._project_btn)

    # ── Selector de rama git (v1: solo locales, checkout bloqueado si dirty) ──
    # Mismo lenguaje visual que el botón de proyecto. Oculto si no hay
    # proyecto/repo; SessionPane._refresh_branch_btn puebla el menú.
    tab._branch_btn = QToolButton()
    tab._branch_btn.setText("⑂ —")
    tab._branch_btn.setStyleSheet(project_button_style(False))
    tab._branch_btn.setToolTip("Rama git del proyecto activo")
    # InstantPopup: el menú abre con UN clic (DelayedPopup — el default —
    # exige mantener presionado; se veía como "no clickeable").
    tab._branch_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    tab._branch_btn.setVisible(False)
    row.addWidget(tab._branch_btn)

    # Progreso de pre-carga inline (absorbe _preload_progress/_preload_status)
    tab._preload_progress = QProgressBar()
    tab._preload_progress.setRange(0, 100)
    tab._preload_progress.setValue(0)
    tab._preload_progress.setMaximumHeight(10)
    tab._preload_progress.setMaximumWidth(120)
    tab._preload_progress.setTextVisible(False)
    tab._preload_progress.setVisible(False)
    tab._preload_progress.setStyleSheet(StyleFactory.progress_bar(COLORS["success"]))
    tab._preload_status = QLabel("")
    tab._preload_status.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
    row.addWidget(tab._preload_progress)
    row.addWidget(tab._preload_status, 1)

    row.addStretch()

    # ── Refrescar: recarga combos/detalle sin
    # reabrir la app (workflows/agentes cambian por YAML a mano).
    tab._refresh_btn = QPushButton("⟳")
    tab._refresh_btn.setStyleSheet(tag_style)
    tab._refresh_btn.setToolTip("Refrescar workflows, agentes y detalle del workflow")
    tab._refresh_btn.clicked.connect(tab._refresh_all)
    row.addWidget(tab._refresh_btn)

    # ── Nueva conversación ──
    tab._new_conv_btn = QPushButton("＋ Nueva")
    tab._new_conv_btn.setStyleSheet(tag_style)
    tab._new_conv_btn.setToolTip("Nueva conversación")
    tab._new_conv_btn.clicked.connect(tab._new_conversation)
    row.addWidget(tab._new_conv_btn)

    # ── Export fusionado (spec §5) ──
    tab._export_btn = QToolButton()
    tab._export_btn.setText("⬇ ▾")
    tab._export_btn.setStyleSheet(tag_style)
    tab._export_btn.setToolTip("Exportar conversación")
    export_menu = QMenu(tab._export_btn)
    for fmt in ("md", "json", "pdf", "html"):
        act = export_menu.addAction(fmt)
        act.triggered.connect(lambda checked=False, f=fmt: tab._download_conversation(fmt=f))
    tab._export_btn.setMenu(export_menu)
    tab._export_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    row.addWidget(tab._export_btn)

    outer.addLayout(row)
    return bar


def project_button_style(active: bool) -> str:
    from desktop.theme import COLORS, ThemeManager

    color = ThemeManager.current().colors.success if active else COLORS["text_dim"]
    return (
        f"QToolButton {{ background: transparent; color: {color}; "
        f"border: 1px dashed {COLORS['border_light']}; border-radius: 6px; "
        f"padding: 3px 8px; font-size: 11px; }}"
        f"QToolButton::menu-indicator {{ subcontrol-position: right center; right: 4px; }}"
    )


def _divider() -> QWidget:
    d = QWidget()
    d.setFixedWidth(1)
    d.setFixedHeight(14)
    d.setStyleSheet(f"background: {COLORS['border_light']};")
    return d
