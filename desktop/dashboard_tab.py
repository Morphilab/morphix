"""Dashboard Tab — lanzador, workflows/agentes, árbol de proyectos y utilidades."""

import html
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import COLORS, StyleFactory

logger = logging.getLogger(__name__)

from desktop.async_helpers import run_async

_ACRONYMS = {"tdd", "bdd", "sdd", "edd", "atdd", "dsl", "api", "ui"}


def _display_workflow_name(slug: str) -> str:
    """Title-case del slug para display ("domain_tdd" → "Domain TDD").

    Solo presentación — el slug interno queda intacto para launch_workflow.
    """
    words = []
    for word in slug.replace("_", " ").split():
        words.append(word.upper() if word in _ACRONYMS else word.capitalize())
    return " ".join(words)


def _truncate_text(text: str, limit: int) -> str:
    """Trunca en límite de palabra con "…" — nunca a mitad de palabra."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.-—")
    return f"{cut}…"


def _workflow_meta(view: dict) -> str:
    """Chip de metadata real del workflow (agentes/tools/proyecto)."""
    parts = []
    agents = view.get("agents_allowed") or []
    tools = view.get("tools_allowed") or []
    if agents:
        parts.append(f"{len(agents)} agente" + ("s" if len(agents) != 1 else ""))
    if tools:
        parts.append(f"{len(tools)} tool" + ("s" if len(tools) != 1 else ""))
    if view.get("project_required"):
        parts.append("requiere proyecto")
    return " · ".join(parts)


class DashboardTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._connect_signals()

    def showEvent(self, event) -> None:
        """Refresca al mostrar el tab — refleja edits hechos en Bots/Maestro."""
        super().showEvent(event)
        run_async(self._load_data())

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(16, 12, 16, 8)
        main.setSpacing(10)

        # ── Saludo + lanzador ──
        head = QVBoxLayout()
        title = QLabel("¿Qué quieres hacer?")
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {COLORS['text_primary']};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = QLabel("Escribe y te lleva al Maestro · o lanza un acceso rápido")
        subtitle.setStyleSheet(f"font-size: 12px; color: {COLORS['text_dim']};")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(title)
        head.addWidget(subtitle)
        main.addLayout(head)

        self.launch_field = QLineEdit()
        self.launch_field.setPlaceholderText("⌕  Desarrolla una función que…")
        self.launch_field.setStyleSheet(
            f"QLineEdit {{ background: {COLORS['bg_surface']}; color: {COLORS['text_primary']}; "
            f"border: 1px solid {COLORS['border_default']}; border-radius: 9px; "
            f"padding: 10px 14px; font-size: 13px; }}"
        )
        self.launch_field.setMaximumWidth(640)
        self.launch_field.returnPressed.connect(self._launch_prompt)
        launch_row = QHBoxLayout()
        launch_row.addStretch()
        launch_row.addWidget(self.launch_field, 5)
        launch_row.addStretch()
        main.addLayout(launch_row)

        # ── Split: acciones (izq) · contexto (der) ──
        split = QHBoxLayout()
        split.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(8)
        wf_header, self.wf_counter = self._section_header("WORKFLOWS")
        left.addWidget(wf_header)
        self.workflows_grid = QGridLayout()
        self.workflows_grid.setSpacing(10)
        # El grid vive en un scroll: 9+ workflows × 88px imponían un mínimo
        # vertical gigante al QSplitter (604px sobre una ventana de ~650) —
        # desbordaba la columna y aplastaba el panel derecho.
        # El stretch interno evita que el viewport estire las filas cuando
        # hay pocas cards (quedaban espaciadas en vez de arriba).
        workflows_inner = QWidget()
        inner_lay = QVBoxLayout(workflows_inner)
        # Margen interno del contenedor: las cards no deben pegarse a los
        # bordes del viewport (3 lados; abajo el stretch lo da gratis).
        inner_lay.setContentsMargins(2, 2, 8, 2)
        inner_lay.addLayout(self.workflows_grid)
        inner_lay.addStretch(1)
        workflows_scroll = QScrollArea()
        workflows_scroll.setWidgetResizable(True)
        workflows_scroll.setStyleSheet(self._thin_scroll_style())
        workflows_scroll.setWidget(workflows_inner)
        left.addWidget(workflows_scroll, 1)

        ag_header, self.ag_counter = self._section_header("AGENTES")
        left.addWidget(ag_header)
        self.agents_flow = QHBoxLayout()
        self.agents_flow.setSpacing(6)
        self.agents_flow.addStretch()
        left.addLayout(self.agents_flow)

        left_wrap = QWidget()
        left_wrap.setLayout(left)
        split.addWidget(left_wrap, 8)

        right = QVBoxLayout()
        prj_header, self.prj_counter = self._section_header("PROYECTOS")
        prj_header.setToolTip("Selector de proyectos — click en uno → Maestro")
        right.addWidget(prj_header)
        self.projects_tree = QLabel("")
        self.projects_tree.setTextFormat(Qt.TextFormat.RichText)
        self.projects_tree.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.projects_tree.setWordWrap(True)
        # El QLabel centra su texto verticalmente por defecto → "code_projects/"
        # quedaba a mitad del scroll fijo. Arriba-izquierda siempre.
        self.projects_tree.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.projects_tree.linkActivated.connect(self._on_project_link)
        self.projects_tree.setStyleSheet(
            f"font-family: 'JetBrains Mono','Fira Code',Consolas,monospace; font-size: 12px; "
            f"color: {COLORS['text_secondary']}; line-height: 1.6; padding: 4px 0; "
            f"background: transparent;"
        )
        # contenedor de altura fija. El sizeHint del QLabel
        # saltaba 640×25px (vacío, frase larga sin wrap) ↔ 122×76px (con
        # proyectos) y el QSplitter re-repartía todo el dashboard al crear o
        # borrar un proyecto (repro offscreen).
        projects_scroll = QScrollArea()
        projects_scroll.setWidgetResizable(True)
        projects_scroll.setFixedHeight(200)
        projects_scroll.setStyleSheet(self._thin_scroll_style())
        projects_scroll.setWidget(self.projects_tree)
        right.addWidget(projects_scroll)

        altas = QHBoxLayout()
        for text, handler in (
            ("＋ Nuevo", self._create_project),
            ("⌂ Importar", self._import_project),
            ("⤓ Clonar", self._clone_project),
        ):
            btn = QPushButton(text)
            btn.setStyleSheet(self._alta_style())
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(handler)
            altas.addWidget(btn)
        right.addLayout(altas)

        # sección de bots — chips pill bajo Proyectos.
        bots_header, self.bots_counter = self._section_header("BOTS")
        right.addWidget(bots_header)
        self.bots_flow = QHBoxLayout()
        self.bots_flow.setSpacing(6)
        self.bots_flow.addStretch()
        right.addLayout(self.bots_flow)
        right.addStretch()

        right_wrap = QWidget()
        right_wrap.setLayout(right)
        # ancho mínimo estable para el QSplitter (que no baile el reparto).
        right_wrap.setMinimumWidth(240)
        split.addWidget(right_wrap, 4)
        main.addLayout(split, 1)

        # ── Pie en split: estado izq · utilidades der ──
        footer = QHBoxLayout()
        footer.setSpacing(6)
        self.mode_icon = QLabel("●")
        self._apply_status_dot_style()
        footer.addWidget(self.mode_icon)
        self.offline_btn = QPushButton("Activar Offline")
        self.offline_btn.setStyleSheet(StyleFactory.ghost_button())
        self.offline_btn.clicked.connect(self._toggle_offline)
        footer.addWidget(self.offline_btn)
        self.self_reflection_cb = QCheckBox("Self-Reflection")
        self.self_reflection_cb.setToolTip("Agentes se auto-revisan")
        self.self_reflection_cb.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 12px;"
        )
        self.self_reflection_cb.toggled.connect(self._toggle_self_reflection)
        footer.addWidget(self.self_reflection_cb)
        footer.addStretch()
        for text, handler in (
            ("Logs", self._open_logs),
            ("lnav", self._open_logs_lnav),
        ):
            btn = QPushButton(text)
            btn.setStyleSheet(StyleFactory.ghost_button())
            btn.clicked.connect(handler)
            footer.addWidget(btn)
        main.addLayout(footer)

    # ── Estilos locales ──

    def _section_header(self, title: str) -> tuple[QWidget, QLabel]:
        """Header de sección: título izq + hairline + contador.

        Devuelve (fila, label de contador) — el caller actualiza el contador
        al repoblar la sección.
        """
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"font-size: 11px; letter-spacing: 2px; color: {COLORS['text_primary']}; "
            f"font-weight: 700;"
        )
        lay.addWidget(lbl)
        counter = QLabel("")
        counter.setStyleSheet(f"font-size: 10px; color: {COLORS['text_dim']};")
        lay.addWidget(counter)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {COLORS['border_default']};")
        lay.addWidget(line, 1)
        return row, counter

    def _thin_scroll_style(self) -> str:
        """Scroll mínimo viable: 5px, sin track ni flechas, solo el handle."""
        return (
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 5px; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: {COLORS['border_light']}; "
            "border-radius: 2px; min-height: 30px; }"
            "QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 60); }"
            "QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }"
            "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
        )

    def _h_divider(self) -> QWidget:
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {COLORS['border_default']};")
        return line

    def _alta_style(self) -> str:
        return (
            f"QPushButton {{ background: transparent; color: {COLORS['text_dim']}; "
            f"border: 1px dashed {COLORS['border_light']}; border-radius: 7px; "
            f"padding: 6px 4px; font-size: 11px; }}"
            f"QPushButton:hover {{ color: {COLORS['text_primary']}; }}"
        )

    def _apply_status_dot_style(self) -> None:
        from core.config import settings

        is_off = settings.offline_mode
        self.mode_icon.setText("●")
        self.mode_icon.setStyleSheet(
            f"font-size: 13px; color: {COLORS['warning'] if is_off else COLORS['success']};"
        )
        self.mode_icon.setToolTip("Offline" if is_off else "Online")

    # ── Señales y datos ──

    def _connect_signals(self):
        from desktop.events import get_signals

        get_signals().offline_changed.connect(lambda offline: self._refresh_offline_indicators())
        get_signals().workspace_changed.connect(self._on_external_workspace_change)

    async def _load_data(self):
        try:
            from core.feature_flags import kairos

            self.self_reflection_cb.blockSignals(True)
            self.self_reflection_cb.setChecked(kairos.get("AGENT_SELF_REFLECTION", False))
            self.self_reflection_cb.blockSignals(False)

            self._refresh_offline_indicators()
            self._refresh_modules()
            self._refresh_projects_tree()

            # bots con try propio — un fallo de BD no tira el resto.
            try:
                from core.bots import BotsService

                bots = await BotsService.list_bots(include_disabled=True)
                self._refresh_bots(bots)
            except Exception:
                logger.exception("Error cargando bots del dashboard")

        except Exception:
            logger.exception("Error cargando datos del dashboard")

    def _refresh_modules(self):
        """Repuebla el grid de Workflows y los chips de Agentes dinámicamente."""
        from agents.registry import agents_registry
        from core.workspaces import get_global_workspaces
        from desktop.services.workflow_view import load_workflow_view
        from orchestration.loader import list_workflows

        ws = get_global_workspaces().current

        # Workflows — grid en scroll, cards con jerarquía
        workflows = list_workflows(ws)
        self.wf_counter.setText(str(len(workflows)))
        while self.workflows_grid.count():
            item = self.workflows_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, wf_name in enumerate(workflows):
            view = load_workflow_view(ws, wf_name) or {}
            desc = _truncate_text(str(view.get("description", "")), 70)
            btn = QPushButton()
            # QPushButton.sizeHint() con texto vacío IGNORA el layout interno
            # (colapsa a ~25px y el grid recorta los labels — cards invisibles
            # sin error en log, reproducido offscreen). El mínimo explícito
            # dimensiona la fila: nombre 16 + desc 2×14 + meta 12 + caja 18.
            btn.setMinimumHeight(74)
            # Claves del dict de compatibilidad: bg_surface→bg_solid token,
            # border_light→border_strong, bg_surface_raised→bg_raised
            # (guard: tests/test_colors_keys_guard.py).
            btn.setStyleSheet(
                f"QPushButton {{ background: {COLORS['bg_surface']}; padding: 0; "
                f"border: 1px solid {COLORS['border_light']}; border-radius: 8px; }}"
                f"QPushButton:hover {{ background: {COLORS['bg_surface_raised']}; "
                f"border-color: {COLORS['border_focus']}; }}"
            )
            body = QVBoxLayout(btn)
            body.setContentsMargins(8, 6, 8, 6)
            body.setSpacing(2)
            rows = [QLabel(_display_workflow_name(wf_name))]
            rows[0].setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {COLORS['text_primary']};"
            )
            if desc:
                desc_label = QLabel(desc)
                desc_label.setWordWrap(True)
                desc_label.setStyleSheet(f"font-size: 10px; color: {COLORS['text_secondary']};")
                rows.append(desc_label)
            meta = _workflow_meta(view)
            if meta:
                meta_label = QLabel(meta)
                meta_label.setStyleSheet(f"font-size: 9px; color: {COLORS['text_dim']};")
                rows.append(meta_label)
            for lbl in rows:
                # Los labels no capturan el ratón: el click cae en la card.
                lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                body.addWidget(lbl)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            desc_full = str(view.get("description", ""))
            if desc_full:
                btn.setToolTip(desc_full)
            btn.clicked.connect(
                lambda checked, n=wf_name: self._navigate("maestro", {"workflow": n})
            )
            self.workflows_grid.addWidget(btn, i // 2, i % 2)

        # Agentes — chips centrados
        agent_names = sorted(agents_registry.list_agents().keys())
        self.ag_counter.setText(str(len(agent_names)))
        while self.agents_flow.count():
            item = self.agents_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.agents_flow.addStretch()
        for agent_name in agent_names:
            profile = agents_registry.get_profile(agent_name)
            tools = profile.get("tools", []) if profile else []
            label = f"{agent_name.capitalize()}" + (f" · {len(tools)}" if tools else "")
            btn = QPushButton(label)
            btn.setStyleSheet(
                f"QPushButton {{ background: {COLORS['bg_surface']}; "
                f"color: {COLORS['text_secondary']}; border: 1px solid {COLORS['border_default']}; "
                f"border-radius: 999px; padding: 4px 10px; font-size: 11px; }}"
                f"QPushButton:hover {{ color: {COLORS['text_primary']}; }}"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda checked, n=agent_name: self._navigate("maestro", {"agent": n})
            )
            self.agents_flow.addWidget(btn)
        self.agents_flow.addStretch()

    def _refresh_bots(self, bots: list[dict]) -> None:
        """Repuebla los chips de Bots — pill como Agentes.

        Enabled → clicable (abre su chat eterno). Disabled → atenuado,
        sin clic. Siempre un chip fantasma '＋ Bot' → pestaña Bots.
        """
        self.bots_counter.setText(str(len(bots)))
        while self.bots_flow.count():
            item = self.bots_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.bots_flow.addStretch()
        for bot in sorted(bots, key=lambda b: str(b.get("slug", ""))):
            name = str(bot.get("display_name") or bot.get("slug", "?"))
            btn = QPushButton(name)
            if bool(bot.get("enabled", True)):
                btn.setStyleSheet(
                    f"QPushButton {{ background: {COLORS['bg_surface']}; "
                    f"color: {COLORS['text_primary']}; "
                    f"border: 1px solid {COLORS['border_light']}; "
                    f"border-radius: 999px; padding: 4px 10px; font-size: 11px; }}"
                    f"QPushButton:hover {{ background: {COLORS['bg_surface_raised']}; }}"
                )
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setToolTip("Abrir chat eterno")
                slug = str(bot.get("slug", ""))
                btn.clicked.connect(lambda checked, s=slug: self._open_bot(s))
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {COLORS['bg_surface']}; "
                    f"color: {COLORS['text_dim']}; "
                    f"border: 1px solid {COLORS['border_default']}; "
                    f"border-radius: 999px; padding: 4px 10px; font-size: 11px; }}"
                )
                btn.setEnabled(False)
                btn.setToolTip("Bot deshabilitado — actívalo en la pestaña Bots")
            self.bots_flow.addWidget(btn)
        self.bots_flow.addStretch()

    def _open_bot(self, slug: str) -> None:
        """Abre el chat eterno del bot y salta al Maestro (camino del Roster)."""
        from desktop.services.bots_service import open_bot_chat

        async def _go() -> int:
            opened = await open_bot_chat(slug)
            return int(opened["conversation_id"])

        def _done(fut) -> None:
            try:
                exc = fut.exception()
            except Exception:  # future cancelado
                return
            if exc is not None:
                logger.warning("Apertura de chat de bot '%s' falló: %s", slug, exc)
                self._status(f"⚠ No se pudo abrir el chat de '{slug}'")
                return
            parent = self.window()
            resume = getattr(parent, "_on_resume_conversation", None)
            if callable(resume):
                resume(fut.result())

        run_async(_go()).add_done_callback(_done)

    # ── Árbol de proyectos ──

    def _refresh_projects_tree(self) -> None:
        """Pinta code_projects/ como árbol de texto con enlaces."""
        from desktop.services.project_service import projects_base

        base = projects_base()
        current = self._current_project_name()
        entries: list[str] = []
        if base.exists():
            entries = sorted(
                d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")
            )
        if not entries:
            self.prj_counter.setText("0")
            self.projects_tree.setText(
                f"<span style='color:{COLORS['text_dim']}'>Sin proyectos — "
                "clona un repo o importa un directorio para dar contexto a los workflows.</span>"
            )
            return
        self.prj_counter.setText(str(len(entries)))
        rows = [f"<span style='color:{COLORS['text_dim']}'>▾ code_projects/</span>"]
        for i, name in enumerate(entries):
            branch = "└─" if i == len(entries) - 1 else "├─"
            active = f" <span style='color:{COLORS['success']}'>●</span>" if name == current else ""
            safe = html.escape(name, quote=True)
            rows.append(
                f"{branch} <a href='{safe}' style='color:{COLORS['text_primary']}; "
                f"text-decoration: underline dotted'>{safe}</a>{active}"
            )
        self.projects_tree.setText("<br>".join(rows))

    def _current_project_name(self) -> str | None:
        maestro = self._maestro()
        root = getattr(maestro, "_current_project_root", None)
        return root.split("/")[-1] if root else None

    def _on_project_link(self, name: str) -> None:
        maestro = self._maestro()
        if maestro is None:
            return
        maestro._switch_project(html.unescape(name))
        self._navigate("maestro")

    # ── Lanzador ──

    def _launch_prompt(self) -> None:
        text = self.launch_field.text().strip()
        if not text:
            return
        maestro = self._maestro()
        if maestro is None:
            return
        setter = getattr(maestro, "set_pending_prompt", None)
        if callable(setter):
            accepted = setter(text)
            if accepted is False:
                # sin sesión libre el texto NO se destruye — feedback de
                # primer nivel y el prompt queda en el campo para reintentar.
                from PySide6.QtWidgets import QMessageBox

                QMessageBox.information(
                    self,
                    "Sesiones ocupadas",
                    "Todas las sesiones de Maestro están ocupadas — detén o "
                    "cierra una e inténtalo de nuevo.",
                )
                return
            self.launch_field.clear()
        self._navigate("maestro")

    # ── Acceso al Maestro y status bar ──

    def _maestro(self):
        parent = self.window()
        return getattr(parent, "maestro", None)

    def _status(self, msg: str) -> None:
        parent = self.window()
        status = getattr(parent, "status", None)
        if status is not None:
            status.showMessage(msg, 5000)

    # ── Altas de proyectos (migradas desde maestro_tab) ──

    def _create_project(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        from desktop.services.project_service import (
            create_project,
            normalize_project_name,
        )

        raw_name, ok = QInputDialog.getText(self, "Nuevo proyecto", "Nombre del proyecto:", text="")
        if not ok or not raw_name:
            return
        name = normalize_project_name(raw_name)
        if not name:
            self._status("❌ Nombre inválido. Usa solo letras, números y _")
            return
        created, root = create_project(name)
        if not created:
            self._status(f"❌ Error creando proyecto: {root}")
            return
        maestro = self._maestro()
        if maestro is not None:
            maestro.switch_project(name, reset_conversation=True)
        self._refresh_projects_tree()
        self._status(f"✅ Proyecto '{name}' creado y activado.")

    def _import_project(self) -> None:
        from pathlib import Path

        from PySide6.QtCore import QThread
        from PySide6.QtCore import Signal as QSignal
        from PySide6.QtWidgets import QFileDialog

        from core.constants import PROJECTS_DIR_NAME
        from desktop.services.project_service import (
            import_project,
            normalize_project_name,
            project_dir,
        )

        src = QFileDialog.getExistingDirectory(self, "Seleccionar proyecto para importar")
        if not src:
            return

        src_path = Path(src)
        name = normalize_project_name(src_path.name)
        if not name:
            self._status("❌ Nombre de proyecto inválido. Usa solo letras, números y _")
            return
        dst = project_dir(name)

        if dst.exists():
            self._status(f"❌ Ya existe un proyecto llamado '{name}'")
            return

        self._status(f"📂 Copiando '{src_path.name}' → {PROJECTS_DIR_NAME}/{name}...")

        worker = getattr(self, "_copy_worker", None)
        if worker is not None and worker.isRunning():
            self._status("Espera al proceso actual")
            return

        class _CopyWorker(QThread):
            done = QSignal(bool, str)

            def __init__(self, src, name):
                super().__init__()
                self._src = src
                self._name = name

            def run(self):
                ok, message = import_project(self._src, self._name)
                self.done.emit(ok, message)

        self._copy_worker = _CopyWorker(str(src_path), name)
        self._copy_worker.done.connect(self._on_import_done)
        self._copy_worker.start()

    def _on_import_done(self, success: bool, error: str) -> None:
        if success:
            self._status(f"✅ Proyecto importado: {error}")
        else:
            logger.warning("Error copiando proyecto: %s", error)
            self._status(f"❌ Error copiando proyecto: {error}")
        self._refresh_projects_tree()

    def _clone_project(self) -> None:
        from PySide6.QtCore import QThread
        from PySide6.QtCore import Signal as QSignal
        from PySide6.QtWidgets import QInputDialog

        from core.constants import PROJECTS_DIR_NAME
        from desktop.services.project_service import clone_project, normalize_project_name

        url, ok = QInputDialog.getText(self, "Clonar repositorio", "URL del repo git:", text="")
        if not ok or not url:
            return
        raw_name, ok = QInputDialog.getText(
            self, "Clonar repositorio", "Nombre del proyecto:", text=""
        )
        if not ok:
            return
        name = normalize_project_name(raw_name) if raw_name else None
        if url and name is None:
            self._status("❌ Nombre inválido. Usa solo letras, números y _")
            return

        self._status(f"📂 Clonando → {PROJECTS_DIR_NAME}/{name or '(auto)'}...")

        worker = getattr(self, "_clone_worker", None)
        if worker is not None and worker.isRunning():
            self._status("Espera al proceso actual")
            return

        class _CloneWorker(QThread):
            done = QSignal(bool, str)

            def __init__(self, url, name):
                super().__init__()
                self._url = url
                self._name = name

            def run(self):
                ok2, message = clone_project(self._url, self._name)
                self.done.emit(ok2, message)

        self._clone_worker = _CloneWorker(url.strip(), name)
        self._clone_worker.done.connect(self._on_clone_done)
        self._clone_worker.start()

    def _on_clone_done(self, success: bool, message: str) -> None:
        if success:
            self._status(f"✅ Proyecto clonado: {message}")
        else:
            self._status(f"❌ Error clonando: {message}")
        self._refresh_projects_tree()

    # ── Workspace ──
    # La UI de workspace vive en el pie del sidebar (MainWindow) desde Task 7;
    # aquí solo queda el reload de datos ante cambios externos.

    def _on_external_workspace_change(self, name: str):
        """Refresh dashboard when workspace changes from another tab."""
        run_async(self._load_data())

    # ── Utilidades ──

    def _toggle_offline(self):
        from core.config import settings
        from desktop.services.config_service import ConfigService

        ConfigService.toggle_offline_mode()
        self._refresh_offline_indicators()
        from desktop.events import get_signals

        get_signals().offline_changed.emit(settings.offline_mode)

    def _refresh_offline_indicators(self):
        """Actualiza los indicadores de estado offline sin recargar todo."""
        from core.config import settings as s

        is_off = s.offline_mode
        self._apply_status_dot_style()
        self.offline_btn.setText("Desactivar Offline" if is_off else "Activar Offline")

    def _toggle_self_reflection(self, enabled: bool):
        from core.feature_flags import kairos

        kairos.set("AGENT_SELF_REFLECTION", enabled)

    def _open_logs(self):
        from desktop.services.dashboard_service import DashboardService

        result = DashboardService.open_logs()
        if not result.get("success"):
            parent = self.window()
            if parent and hasattr(parent, "status"):
                parent.status.showMessage(f"Error abriendo logs: {result.get('message', '')}", 5000)

    def _open_logs_lnav(self):
        from desktop.services.dashboard_service import DashboardService

        result = DashboardService.open_logs_lnav()
        if not result.get("success"):
            parent = self.window()
            if parent and hasattr(parent, "status"):
                parent.status.showMessage(f"Error con lnav: {result.get('message', '')}", 5000)

    def _navigate(self, route: str, context: dict | None = None):
        parent = self.window()
        if parent and hasattr(parent, "tabs"):
            tabs = parent.tabs
            # solo rutas con llamador real — "integraciones" apuntaba a
            # una tab inexistente y las demás no tenían ningún invocador.
            tab_map = {"maestro": "Maestro", "bots": "Bots"}
            target = tab_map.get(route)
            if target:
                for i in range(tabs.count()):
                    if tabs.tabText(i) == target:
                        widget = tabs.widget(i)
                        if route == "maestro" and context and hasattr(widget, "launch_workflow"):
                            if "workflow" in context:
                                ok = widget.launch_workflow(context["workflow"])
                            elif "agent" in context:
                                ok = widget.launch_agent(context["agent"])
                            else:
                                ok = True
                            if ok is False:
                                # la card no se lanzó — sin sesiones libres o
                                # proyecto cancelado; feedback explícito en
                                # vez de un log enterrado en otra pestaña.
                                from PySide6.QtWidgets import QMessageBox

                                QMessageBox.information(
                                    self,
                                    "Workflow no lanzado",
                                    "No se lanzó el workflow: no había sesiones "
                                    "libres en Maestro o se canceló la selección "
                                    "de proyecto.",
                                )
                                break
                        tabs.setCurrentIndex(i)
                        break
