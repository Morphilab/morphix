# desktop/bots_tab.py — pestaña "Bots" v2 (contrato 08, capa desktop)
"""Contenedor con 3 sub-pestañas: Bots (roster/crear), Rutinas, Grupos.

Patrón ConfigTab: QTabWidget interno estilizado con StyleFactory.tab_widget().
Reglas UI:
- Clic en fila → ensure_open (adopt-before-mint fail-closed). Jamás se
  guarda/resuelve un session-id propio de la pestaña: la fila canónica ES la
  identidad y el preview sale de la misma resolución que el clic.
- Los chats eternos no aparecen en Historial (sweep + filtro repo).
- Señal ``open_conversation`` hacia MainWindow para montar la conversación
  (el contenedor la re-emite desde RosterPane.open_requested).
- Opción A: un único botón primario por sub-pestaña (la acción de crear);
  los botones ⟳ de refresco son secondary_button.
- workspace_changed invalida y recarga los 3 paneles.
"""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core import bots_groups as groups_mod
from core.bots import BotError, BotsService
from core.bots_routines import (
    create_routine,
    list_routines,
    set_routine_enabled,
)
from desktop.async_helpers import run_async
from desktop.services import bots_service
from desktop.theme import COLORS, StyleFactory

logger = logging.getLogger(__name__)


# ============================================================
# Contenedor
# ============================================================
class BotsTab(QWidget):
    """Pestaña Bots v2: 3 sub-pestañas Bots/Rutinas/Grupos (patrón ConfigTab)."""

    open_conversation = Signal(int)  # conversation_id del chat eterno

    def __init__(self, parent=None):
        super().__init__(parent)
        self.roster = RosterPane()
        self.routines_pane = RoutinesPane()
        self.groups_pane = GroupsPane()
        self.roster.open_requested.connect(self.open_conversation.emit)

        layout = QVBoxLayout(self)
        self.inner_tabs = QTabWidget()
        self.inner_tabs.setStyleSheet(StyleFactory.tab_widget())
        self.inner_tabs.addTab(self.roster, "Bots")
        self.inner_tabs.addTab(self.routines_pane, "Rutinas")
        self.inner_tabs.addTab(self.groups_pane, "Grupos")
        layout.addWidget(self.inner_tabs)

        # el roster/rutinas/salas son por-workspace — sin esta conexión,
        # tras un switch la lista mostraba datos del schema anterior.
        from desktop.events import get_signals

        get_signals().workspace_changed.connect(self._on_workspace_changed)

    def _on_workspace_changed(self, _ws: str) -> None:
        """Invalida filas obsoletas y recarga los 3 paneles del nuevo schema."""
        self.roster.invalidate()
        self.routines_pane.refresh_async()
        self.groups_pane.refresh_async()


# ============================================================
# Sub-pestaña 1: Bots (identidad / crear) — RosterPane
# ============================================================
class RosterPane(QWidget):
    """Roster del workspace: crear/clonar/eliminar y abrir chat eterno."""

    open_requested = Signal(int)  # conversation_id del chat eterno

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._clicked_slug: str | None = None
        self._roster_status_base = ""
        # una apertura en curso — clic + doble-clic no disparan dos
        # ensure_open ni dos emisiones de open_requested.
        self._opening: bool = False
        self._build_ui()
        run_async(self.refresh())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel("<b>Bots</b> — identidad persistente del workspace")
        title.setStyleSheet(f"color:{COLORS['text_dim']};")
        header.addWidget(title)
        header.addStretch(1)

        self.btn_refresh = QPushButton("⟳ Refrescar")
        self.btn_refresh.setStyleSheet(StyleFactory.secondary_button())
        self.btn_refresh.clicked.connect(lambda: run_async(self._on_refresh()))
        header.addWidget(self.btn_refresh)
        layout.addLayout(header)

        actions = QHBoxLayout()
        self.btn_new = QPushButton("+ Nuevo bot")
        self.btn_new.setStyleSheet(StyleFactory.primary_button())
        self.btn_edit = QPushButton("✎ Editar")
        self.btn_clone = QPushButton("⧉ Clonar")
        self.btn_delete = QPushButton("🗑 Eliminar")
        self.btn_open = QPushButton("Abrir chat eterno")
        for b in (self.btn_edit, self.btn_clone, self.btn_delete, self.btn_open):
            b.setStyleSheet(StyleFactory.secondary_button())
            actions.addWidget(b)
        actions.addWidget(self.btn_new)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(StyleFactory.list_widget())
        self.list_widget.itemClicked.connect(self._on_roster_clicked)
        self.list_widget.itemDoubleClicked.connect(lambda _i: self._on_open_clicked())
        layout.addWidget(self.list_widget)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{COLORS['text_dim']};")
        layout.addWidget(self.status)

        self.btn_new.clicked.connect(self._on_new_bot)
        self.btn_edit.clicked.connect(self._on_edit_template)
        self.btn_clone.clicked.connect(self._on_clone)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_open.clicked.connect(self._on_open_clicked)

    # ── Datos ────────────────────────────────────────────────────

    def invalidate(self) -> None:
        """Invalida filas obsoletas y recarga el roster."""
        self.list_widget.clear()
        self._rows = []
        self.status.setText("↻ Cargando roster del workspace…")
        run_async(self.refresh())

    def refresh_async(self) -> None:
        run_async(self.refresh())

    async def refresh(self) -> None:
        """Recarga roster + preview canónico server-side."""
        try:
            self._rows = await bots_service.roster_with_previews()
        except Exception as e:
            logger.warning("roster bots falló: %s", e)
            self.status.setText(f"⚠ No se pudo cargar el roster: {e}")
            return
        self.list_widget.clear()
        born = 0
        for row in self._rows:
            canonical = row.get("canonical")
            if canonical:
                born += 1
                mark, tail = "●", f"   → conv #{canonical['conversation_id']}"
            else:
                mark, tail = "○", ""
            item = QListWidgetItem(f"{mark} @{row['slug']} — {row['display_name']}{tail}")
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.list_widget.addItem(item)
        self._roster_status_base = f"{len(self._rows)} bot(s) · {born} chat(s) eterno(s)"
        self.status.setText(self._roster_status_base)

    def _on_roster_clicked(self, item) -> None:
        """Toggle-deselect: Qt no deselecciona al re-clicar."""
        slug = self._selected_slug()
        if self._clicked_slug == slug and slug is not None:
            self._clicked_slug = None
            self.list_widget.setCurrentRow(-1)
            self.status.setText(self._roster_status_base)
            return
        self._clicked_slug = slug
        if slug:
            self.status.setText(f"seleccionado @{slug} — re-clic para deseleccionar")

    def current_row(self) -> dict | None:
        it = self.list_widget.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    # ── Slots ────────────────────────────────────────────────────

    async def _on_refresh(self) -> None:
        await self.refresh()

    def _selected_slug(self) -> str | None:
        row = self.current_row()
        return row["slug"] if row else None

    def _on_open_clicked(self) -> None:
        # dedupe — una sola apertura en vuelo por roster.
        if self._opening:
            return
        slug = self._selected_slug()
        if not slug:
            self.status.setText("Selecciona un bot primero")
            return

        async def _open():
            opened = await bots_service.open_bot_chat(slug)
            await self.refresh()
            return int(opened["conversation_id"])

        self._opening = True
        self.btn_open.setEnabled(False)

        def _done(fut) -> None:
            try:
                self._emit_after_open(fut)
            finally:
                self._opening = False
                self.btn_open.setEnabled(True)

        run_async(_open()).add_done_callback(_done)

    def _emit_after_open(self, fut) -> None:
        """El future de run_async entrega el id del chat eterno al montarse."""
        try:
            exc = fut.exception()
        except Exception:  # future cancelado
            return
        if exc is not None:
            logger.warning("apertura de chat eterno falló: %s", exc)
            self.status.setText(f"⚠ {exc}")
            return
        cid = fut.result()
        if cid is None:
            return
        self.open_requested.emit(int(cid))

    def _on_new_bot(self) -> None:
        """Crear bot = crear plantilla.

        El formulario escribe el YAML del workspace; el upsert del switch
        (sync_bot_templates) proyecta la fila DB.
        """
        slug = _ask_text(self, "Nuevo bot (plantilla)", "Slug (a-z0-9):")
        if slug is None:
            return
        from core.bot_templates import BotTemplate

        try:
            BotTemplate.model_validate({"slug": slug.strip().lower(), "soul_md": "x"})
        except Exception as e:
            QMessageBox.warning(self, "Nuevo bot", f"Slug inválido: {e}")
            return
        self._open_template_editor(slug.strip().lower(), on_saved=self.refresh_async)

    def _on_edit_template(self) -> None:
        slug = self._selected_slug()
        if not slug:
            self.status.setText("Selecciona un bot para editar su plantilla")
            return
        self._open_template_editor(slug, on_saved=self.refresh_async)

    def _open_template_editor(self, slug: str, *, on_saved) -> None:
        """Diálogo de plantilla: crear (defaults) o editar (valores del YAML)."""
        from PySide6.QtWidgets import (
            QCheckBox,
            QDialog,
            QDialogButtonBox,
            QDoubleSpinBox,
            QFormLayout,
            QLineEdit,
            QPlainTextEdit,
        )

        from core.bot_templates import read_bot_template_fields

        existing = read_bot_template_fields(self._current_workspace(), slug)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Plantilla de bot @{slug}")
        dlg.resize(620, 560)
        form = QFormLayout(dlg)

        display = QLineEdit(str(existing.get("display_name") or slug) if existing else slug)
        desc = QLineEdit(str(existing.get("description") or "") if existing else "")
        soul = QPlainTextEdit()
        soul.setPlainText(str(existing.get("soul_md") or "") if existing else "")
        soul.setPlaceholderText(
            "Personalidad del bot (SOUL). Vacío no se permite: un bot sin SOUL no existe."
        )
        model = QLineEdit(str(existing.get("model") or "") if existing else "")
        model.setPlaceholderText("opcional — ej. deepseek-v4-flash (vacío = rol global)")
        temperature = QDoubleSpinBox()
        temperature.setRange(0.0, 2.0)
        temperature.setSingleStep(0.1)
        if existing and existing.get("temperature") is not None:
            temperature.setValue(float(existing["temperature"]))
        else:
            temperature.setValue(0.7)
        tools = QLineEdit(", ".join(existing.get("tool_names") or []) if existing else "")
        tools.setPlaceholderText("coma-separadas — ej. web_fetch, memory_inspector")
        skills = QLineEdit(", ".join(existing.get("skill_allowlist") or []) if existing else "")
        skills.setPlaceholderText("coma-separadas (opcional)")
        can_pause = QCheckBox("Puede pausar a preguntar (solo chat canónico)")
        can_pause.setChecked(bool(existing.get("can_pause")) if existing else False)

        form.addRow("Nombre visible:", display)
        form.addRow("Descripción:", desc)
        form.addRow("SOUL:", soul)
        form.addRow("Modelo:", model)
        form.addRow("Temperatura:", temperature)
        form.addRow("Tools:", tools)
        form.addRow("Skills:", skills)
        form.addRow("", can_pause)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if not dlg.exec():
            return

        soul_text = soul.toPlainText().strip()
        if not soul_text:
            QMessageBox.warning(self, "Plantilla", "El SOUL no puede estar vacío.")
            return
        payload = {
            "display_name": display.text().strip() or slug,
            "description": desc.text().strip(),
            "soul_md": soul_text,
            "provider": None,
            "model": model.text().strip() or None,
            "temperature": float(temperature.value()),
            "tool_names": [t.strip() for t in tools.text().split(",") if t.strip()],
            "skill_allowlist": [s.strip() for s in skills.text().split(",") if s.strip()],
            "can_pause": can_pause.isChecked(),
            "dm_enabled": bool(existing.get("dm_enabled", True)) if existing else True,
            "agent": (
                str(existing.get("agent") or "conversacional") if existing else "conversacional"
            ),
        }

        from core.bot_templates import write_bot_template

        async def _save():
            write_bot_template(self._current_workspace(), slug, payload)
            from core.bot_templates import sync_bot_templates

            await sync_bot_templates(self._current_workspace())
            return slug

        def _done(fut) -> None:
            try:
                fut.result()
                self.status.setText(f"plantilla @{slug} guardada (YAML→DB)")
            except Exception as e:
                QMessageBox.warning(self, "Plantilla", f"Validación falló: {e}")
            finally:
                on_saved()

        run_async(_save()).add_done_callback(_done)

    def _current_workspace(self) -> str:
        from core.workspaces import get_global_workspaces

        return get_global_workspaces().current

    def _on_clone(self) -> None:
        """Clonar bot = duplicar plantilla YAML (template-first)."""
        slug = self._selected_slug()
        if not slug:
            self.status.setText("Selecciona un bot para clonar")
            return
        from core.bot_templates import read_bot_template_fields, write_bot_template

        existing = read_bot_template_fields(self._current_workspace(), slug)
        if existing is None:
            QMessageBox.warning(
                self, "Clonar", f"El bot @{slug} no tiene plantilla YAML exportable."
            )
            return
        new_slug = _ask_text(self, "Clonar bot", f"Slug nuevo (base: {slug}-2):")
        if new_slug is None:
            return
        new_slug = new_slug.strip().lower()

        async def _clone():
            write_bot_template(self._current_workspace(), new_slug, dict(existing))
            from core.bot_templates import sync_bot_templates

            await sync_bot_templates(self._current_workspace())
            await self.refresh()

        run_async(_clone())

    def _on_delete(self) -> None:
        slug = self._selected_slug()
        if not slug:
            self.status.setText("Selecciona un bot para eliminar")
            return
        confirm = QMessageBox.question(
            self,
            "Eliminar bot",
            f"¿Eliminar @{slug}?\nSe borran su fila DB, su plantilla YAML del workspace, "
            "su chat eterno, rutinas e inbox (CASCADE).",
        )
        if confirm is not QMessageBox.StandardButton.Yes:
            return

        async def _delete():
            try:
                from core.bot_templates import delete_bot_template_file

                delete_bot_template_file(self._current_workspace(), slug)
                await BotsService.delete_bot(slug)
                await bots_service.run_sweep()
            finally:
                await self.refresh()

        run_async(_delete())


# ============================================================
# Sub-pestaña 2: Rutinas (dimensión tiempo) — RoutinesPane
# ============================================================
class RoutinesPane(QWidget):
    """Administración de rutinas: lista con estados, crear/pausar/eliminar.

    Modo de entrega EXPLÍCITO en el diálogo (history/bot-chat), desacoplado
    de la selección del roster."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        run_async(self.refresh())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel("<b>Rutinas</b> — la dimensión tiempo")
        title.setStyleSheet(f"color:{COLORS['text_dim']};")
        header.addWidget(title)
        header.addStretch(1)
        self.btn_rt_refresh = QPushButton("⟳ Refrescar")
        self.btn_rt_refresh.setStyleSheet(StyleFactory.secondary_button())
        self.btn_rt_refresh.clicked.connect(lambda: run_async(self.refresh()))
        header.addWidget(self.btn_rt_refresh)
        layout.addLayout(header)

        actions = QHBoxLayout()
        self.btn_rt_new = QPushButton("⏰ Nueva rutina")
        self.btn_rt_new.setStyleSheet(StyleFactory.primary_button())
        self.btn_rt_edit = QPushButton("✎ Editar")
        self.btn_rt_edit.setStyleSheet(StyleFactory.secondary_button())
        self.btn_rt_toggle = QPushButton("⏯ Pausar/reanudar")
        self.btn_rt_del = QPushButton("🗑 Eliminar")
        for b in (self.btn_rt_edit, self.btn_rt_toggle, self.btn_rt_del):
            b.setStyleSheet(StyleFactory.secondary_button())
            actions.addWidget(b)
        actions.addWidget(self.btn_rt_new)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.routines_list = QListWidget()
        self.routines_list.setStyleSheet(StyleFactory.list_widget())
        self.routines_list.itemClicked.connect(self._on_routine_selected)
        layout.addWidget(self.routines_list)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{COLORS['text_dim']};")
        layout.addWidget(self.status)

        self.btn_rt_new.clicked.connect(self._on_new_routine)
        self.btn_rt_edit.clicked.connect(self._on_edit_routine)
        self.btn_rt_toggle.clicked.connect(self._on_toggle_routine)
        self.btn_rt_del.clicked.connect(self._on_delete_routine)

    # ── Datos ────────────────────────────────────────────────────

    def refresh_async(self) -> None:
        run_async(self.refresh())

    async def refresh(self) -> None:
        """Lista de rutinas por-workspace con estado (▶/⏸) y selección estable.

        Los fallos consecutivos y el dead-letter
        son VISIBLES — 🛑 al llegar al tope (re-firing acotado a 1/día),
        contador ✕N antes."""
        from core.bots_routines import ROUTINE_DEAD_LETTER

        try:
            rows = await list_routines()
        except Exception as e:
            logger.warning("listado de rutinas falló: %s", e)
            return
        current = self._current_routine_id()
        self.routines_list.clear()
        for r in rows:
            target = f"@{r.get('slug')}" if r.get("slug") else "hist"
            mark = "▶" if bool(r.get("enabled")) else "⏸"
            failures = int(r.get("failure_count") or 0)
            if failures >= ROUTINE_DEAD_LETTER:
                color = COLORS["error"]
                fail_sfx = f" · 🛑 dead-letter ({failures} fallos, re-intento diario)"
            elif failures > 0:
                color = COLORS["warning"]
                fail_sfx = f" · ✕{failures} fallos"
            else:
                color = COLORS["success"] if bool(r.get("enabled")) else COLORS["warning"]
                fail_sfx = ""
            nxt = r.get("next_run_at")
            nxt_s = f"próxima {_fmt_local_schedule(nxt)}" if nxt else "sin próxima"
            err = r.get("last_error")
            err_sfx = f" · ⚠ {str(err)[:60]}" if err else ""
            item = QListWidgetItem(
                f"{mark} {r['name']} · {r['schedule']} · {target} · {nxt_s}{fail_sfx}{err_sfx}"
            )
            item.setForeground(QColor(color))
            item.setData(Qt.ItemDataRole.UserRole, int(r["id"]))
            self.routines_list.addItem(item)
        if current is not None:
            for i in range(self.routines_list.count()):
                if int(self.routines_list.item(i).data(Qt.ItemDataRole.UserRole)) == current:
                    self.routines_list.setCurrentItem(self.routines_list.item(i))
                    break

    def _current_routine_id(self) -> int | None:
        it = self.routines_list.currentItem()
        data = it.data(Qt.ItemDataRole.UserRole) if it else None
        return int(data) if data is not None else None

    def _on_routine_selected(self, _item) -> None:
        rid = self._current_routine_id()
        if rid is not None:
            run_async(self._status_for_routine(rid))

    def _on_edit_routine(self) -> None:
        """Edita la rutina seleccionada sin re-crearla."""
        rid = self._current_routine_id()
        if rid is None:
            self.status.setText("Selecciona una rutina para editarla")
            return

        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit

        async def _open_dialog() -> None:
            try:
                rows = await list_routines()
            except Exception:
                self.status.setText("No se pudo leer la rutina")
                return
            cur = next((r for r in rows if int(r["id"]) == rid), None)
            if cur is None:
                return
            dlg = QDialog(self)
            dlg.setWindowTitle(f"Editar rutina — {cur['name']}")
            form = QFormLayout(dlg)
            ed_name = QLineEdit(str(cur.get("name") or ""))
            ed_schedule = QLineEdit(str(cur.get("schedule") or ""))
            ed_prompt = QLineEdit(str(cur.get("prompt") or ""))
            ed_prompt.setMinimumWidth(420)
            form.addRow("Nombre:", ed_name)
            form.addRow("Schedule ('30m','every 2h',cron5,ISO):", ed_schedule)
            form.addRow("Prompt:", ed_prompt)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(dlg.accept)
            buttons.rejected.connect(dlg.reject)
            form.addRow(buttons)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            from core.bots_routines import update_routine

            try:
                ok = await update_routine(
                    rid,
                    name=ed_name.text().strip() or None,
                    schedule=ed_schedule.text().strip() or None,
                    prompt=ed_prompt.text(),
                )
            except Exception as e:
                QMessageBox.warning(self, "Rutina", f"No se pudo actualizar: {e}")
                return
            if not ok:
                QMessageBox.warning(
                    self,
                    "Rutina",
                    "No se pudo actualizar (schedule inválido o nada que cambiar).",
                )
                return
            self.status.setText("✎ Rutina actualizada")
            await self.refresh()

        run_async(_open_dialog())

    async def _status_for_routine(self, rid: int) -> None:
        from core.bots_routines import ROUTINE_DEAD_LETTER

        try:
            rows = await list_routines()
        except Exception:
            return
        cur = next((r for r in rows if int(r["id"]) == rid), None)
        if not cur:
            return
        nxt = cur.get("next_run_at")
        nxt_s = _fmt_local_schedule(nxt) if nxt else "sin próxima"
        base = (
            f"rutina '{cur['name']}' ({cur['schedule']}) · "
            f"{'▶ activa' if bool(cur['enabled']) else '⏸ pausada'} · próxima {nxt_s}"
        )
        failures = int(cur.get("failure_count") or 0)
        if failures >= ROUTINE_DEAD_LETTER:
            base += f" · 🛑 dead-letter ({failures} fallos) — edita o re-habilita para resetear"
        elif failures:
            base += f" · ✕{failures} fallos consecutivos"
        if cur.get("last_error"):
            self.status.setText(f"{base} · ⚠ falló: {str(cur['last_error'])[:80]}")
        else:
            self.status.setText(base)

    # ── Slots ────────────────────────────────────────────────────

    def _on_new_routine(self):
        # Modo de entrega EXPLÍCITO: desacoplado de la selección
        # del roster; los slugs salen del servicio, no del pane de roster.
        from PySide6.QtWidgets import QInputDialog

        modos = ["history (para mí — conversación ⏰ visible)", "bot-chat (turno real a un bot)"]
        modo, ok = QInputDialog.getItem(self, "Rutina", "Modo de entrega:", modos, 0, False)
        if not ok:
            return
        deliver = "history" if modo.startswith("history") else "bot-chat"

        async def _pick_and_create():
            slug = None
            if deliver == "bot-chat":
                bots = await BotsService.list_bots(include_disabled=True)
                slugs = [b["slug"] for b in bots if b.get("slug")]
                if not slugs:
                    return "NO_BOTS"
                chosen, ok2 = QInputDialog.getItem(
                    self, "Rutina", "Bot destinatario:", slugs, 0, False
                )
                if not ok2 or not chosen:
                    return None
                slug = chosen
            name = _ask_text(self, "Rutina", "Nombre:")
            if not name:
                return None
            schedule = _ask_text(self, "Rutina", "Schedule ('30m','every 2h',cron5,ISO):") or ""
            prompt = _ask_text(self, "Rutina", "Prompt:") or ""
            await self._create_routine(name.strip(), schedule.strip(), prompt, slug, deliver)
            return None

        def _done(fut) -> None:
            try:
                res = fut.result()
            except Exception:  # futuro cancelado
                return
            if res == "NO_BOTS":
                QMessageBox.information(self, "Rutina", "Crea un bot primero.")

        run_async(_pick_and_create()).add_done_callback(_done)

    async def _create_routine(
        self, name: str, schedule: str, prompt: str, slug: str | None, deliver: str
    ) -> None:
        try:
            created = await create_routine(
                name,
                schedule,
                prompt=prompt,
                slug=slug or None,
                deliver=deliver,
            )
        except Exception as e:
            QMessageBox.warning(self, "Rutina", str(e))
            return
        await self.refresh()
        new_id = int(created.get("id") or 0)
        for i in range(self.routines_list.count()):
            if int(self.routines_list.item(i).data(Qt.ItemDataRole.UserRole)) == new_id:
                self.routines_list.setCurrentItem(self.routines_list.item(i))
                break

    async def _toggle_routine(self, rid: int) -> None:
        rows = await list_routines()
        cur = next((r for r in rows if int(r["id"]) == rid), None)
        if not cur:
            return
        await set_routine_enabled(rid, not bool(cur["enabled"]))
        await self.refresh()

    def _on_toggle_routine(self):
        rid = self._current_routine_id()
        if rid is None:
            QMessageBox.information(self, "Rutina", "Selecciona una rutina primero.")
            return
        run_async(self._toggle_routine(rid))

    async def _delete_routine(self, rid: int) -> None:
        from core.bots_routines import delete_routine

        await delete_routine(rid)
        await self.refresh()

    def _on_delete_routine(self):
        rid = self._current_routine_id()
        if rid is None:
            QMessageBox.information(self, "Rutina", "Selecciona una rutina primero.")
            return
        if (
            QMessageBox.question(self, "Rutina", "¿Eliminar la rutina seleccionada?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        run_async(self._delete_routine(rid))


# ============================================================
# Sub-pestaña 3: Grupos (salas / reunión) — GroupsPane
# ============================================================
class GroupsPane(QWidget):
    """Salas de reunión entre bots: crear/postear/memoria/disolver + log."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rooms_loaded = False
        self._build_ui()
        run_async(self.refresh())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel("<b>Grupos</b> — salas de reunión")
        title.setStyleSheet(f"color:{COLORS['text_dim']};")
        header.addWidget(title)
        header.addStretch(1)
        self.btn_rooms_refresh = QPushButton("⟳ Salas")
        self.btn_rooms_refresh.setStyleSheet(StyleFactory.secondary_button())
        self.btn_rooms_refresh.clicked.connect(lambda: run_async(self._refresh_rooms()))
        header.addWidget(self.btn_rooms_refresh)
        layout.addLayout(header)

        actions = QHBoxLayout()
        self.btn_gr_new = QPushButton("👥 Nuevo grupo")
        self.btn_gr_new.setStyleSheet(StyleFactory.primary_button())
        self.btn_gr_post = QPushButton("↩ Enviar al grupo")
        self.btn_gr_mem = QPushButton("🧠 Memoria de sala")
        self.btn_gr_disband = QPushButton("🗑 Disolver")
        for b in (self.btn_gr_post, self.btn_gr_mem, self.btn_gr_disband):
            b.setStyleSheet(StyleFactory.secondary_button())
            actions.addWidget(b)
        actions.addWidget(self.btn_gr_new)
        actions.addStretch(1)
        layout.addLayout(actions)

        room_row = QHBoxLayout()
        self.rooms_combo = QComboBox()
        self.rooms_combo.setMinimumWidth(240)
        room_row.addWidget(QLabel("Sala:"))
        room_row.addWidget(self.rooms_combo, 1)
        layout.addLayout(room_row)

        self.room_log = QTextBrowser()
        self.room_log.setReadOnly(True)
        self.room_log.setMaximumHeight(220)
        self.room_log.setPlaceholderText("Log de la sala — elige una sala arriba")
        self.room_log.setStyleSheet(StyleFactory.text_browser_log())
        layout.addWidget(self.room_log)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{COLORS['text_dim']};")
        layout.addWidget(self.status)

        self.btn_gr_new.clicked.connect(self._on_new_group)
        self.btn_gr_post.clicked.connect(self._on_post_group)
        self.btn_gr_mem.clicked.connect(self._on_room_memory)
        self.btn_gr_disband.clicked.connect(self._on_disband_group)
        self.rooms_combo.currentIndexChanged.connect(self._on_room_selected)

    # ── Datos ────────────────────────────────────────────────────

    def refresh_async(self) -> None:
        run_async(self.refresh())

    async def refresh(self) -> None:
        await self._refresh_rooms()

    def _current_room_id(self) -> str | None:
        """Sala activa derivada del combo (no solo de la creación en sesión)."""
        if self.rooms_combo.count() == 0:
            return None
        return str(self.rooms_combo.currentData() or "") or None

    async def _refresh_rooms(self) -> None:
        """Recarga el combo de salas (por-workspace) y el log de la activa."""
        try:
            rooms = await groups_mod.list_rooms()
        except Exception as e:
            logger.warning("listado de salas falló: %s", e)
            self.status.setText(f"⚠ No se pudo listar salas: {e}")
            self._rooms_loaded = True
            return
        current = self._current_room_id()
        current_ids = {r["id"] for r in rooms}
        self.rooms_combo.blockSignals(True)
        self.rooms_combo.clear()
        for room in rooms:
            label = f"{room['name']} — @{room['owner_bot_slug']} · {len(room['members'])}"
            self.rooms_combo.addItem(label, room["id"])
        if current and current in current_ids:
            idx = next(i for i, r in enumerate(rooms) if r["id"] == current)
            self.rooms_combo.setCurrentIndex(idx)
        elif rooms:
            self.rooms_combo.setCurrentIndex(0)
        self.rooms_combo.blockSignals(False)
        self._rooms_loaded = True
        await self._load_room_log()

    def _room_log_text(self, msgs: list[dict]) -> str:
        return "\n".join(f"@{m['author']}: {m['content']}" for m in msgs)

    async def _load_room_log(self) -> None:
        rid = self._current_room_id()
        if not rid:
            self.room_log.setPlainText("")
            return
        try:
            msgs = await groups_mod.messages_of(rid, limit=100)
        except Exception as e:
            logger.warning("log de sala falló: %s", e)
            self.room_log.setPlainText(f"⚠ no se pudo cargar el log: {e}")
            return
        self.room_log.setPlainText(self._room_log_text(msgs))

    def _on_room_selected(self, _index: int) -> None:
        run_async(self._load_room_log())

    # ── Slots ────────────────────────────────────────────────────

    def _on_new_group(self):
        name = _ask_text(self, "Grupo", "Nombre de la sala:") or ""
        members = _ask_members(self)
        if members is None:
            return
        clean = members
        if len(clean) < 2:
            QMessageBox.information(self, "Grupo", "Se requieren al menos 2 miembros.")
            return

        async def _mk():
            try:
                room = await groups_mod.create_room(name, clean[0], clean)
            except BotError as e:
                self.status.setText(f"⚠ {e}")
                return None
            await self._refresh_rooms()
            for i in range(self.rooms_combo.count()):
                if self.rooms_combo.itemData(i) == room["id"]:
                    self.rooms_combo.setCurrentIndex(i)
                    break
            return room["id"]

        run_async(_mk())

    def _on_post_group(self):
        rid = self._current_room_id()
        if not rid:
            QMessageBox.information(self, "Grupo", "Crea/selecciona una sala primero.")
            return
        text_in = _ask_text(self, f"Sala {rid}", "Mensaje:") or ""
        if not text_in:
            return
        run_async(self._post_to_room(rid, text_in))

    def _on_room_memory(self):
        rid = self._current_room_id()
        if not rid:
            QMessageBox.information(self, "Grupo", "Selecciona una sala primero.")
            return
        run_async(self._show_room_memory(rid))

    async def _room_memory_text(self, rid: str) -> str:
        """Transcripción 'Group: <rid>' por miembro (memoria por sala)."""
        try:
            transcripts = await groups_mod.room_member_transcripts(rid)
        except Exception as e:
            return f"⚠ no se pudo leer la memoria de sala: {e}"
        if not transcripts:
            return "(sin sesiones miembro aún — postea un mensaje primero)"
        parts: list[str] = []
        for slug in sorted(transcripts):
            parts.append(f"── @{slug} ──")
            for m in transcripts[slug]:
                tag = "usuario" if m["role"] == "user" else "bot"
                parts.append(f"[{tag}] {m['content']}")
        return "\n".join(parts)

    async def _show_room_memory(self, rid: str) -> None:
        text = await self._room_memory_text(rid)
        from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Memoria de sala {rid}")
        dlg.resize(560, 420)
        lay = QVBoxLayout(dlg)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        lay.addWidget(editor)
        dlg.exec()

    async def _post_to_room(self, rid: str, text_in: str) -> dict | None:
        from orchestration.bots_groups_drive import run_room_turn

        try:
            res = await run_room_turn(rid, text_in)
        except Exception as e:
            self.status.setText(f"⚠ ronda falló: {e}")
            return None
        await self._load_room_log()
        responders = ", ".join(res.get("responders") or [])
        self.status.setText(
            f"ronda {res.get('status')}: respondieron {responders or 'nadie'} · "
            f"{res.get('passes', 0)} pass · {len(res.get('stranded') or [])} stranded"
        )
        return res

    def _on_disband_group(self):
        rid = self._current_room_id()
        if not rid:
            return

        async def _db():
            await groups_mod.disband_room(rid)
            await self._refresh_rooms()
            self.status.setText(f"Sala {rid} disuelta")

        run_async(_db())


def _ask_members(parent: QWidget) -> list[str] | None:
    """Checklist de bots habilitados (2-6): la sala se arma marcando, no
    escribiendo slugs. None = cancelado."""
    from PySide6.QtWidgets import QDialog, QLabel, QListWidget, QPushButton, QVBoxLayout

    from core.bots import BotsService

    try:
        # Lectura corta sincrona para el dialogo modal: run_async es
        # fire-and-forget (el Future no resuelve sin bombear eventos) y aqui
        # se necesita el valor YA. run_async autocrea loop despues, asi que
        # el policy-poisoning de asyncio.run esta curado.
        import asyncio

        rows = asyncio.run(BotsService.list_bots(include_disabled=True))
        bots = [b for b in rows if b.get("enabled", True)]
    except Exception as e:  # sin roster no hay sala que armar
        QMessageBox.warning(parent, "Grupo", f"No se pudo cargar el roster: {e}")
        return None

    dlg = QDialog(parent)
    dlg.setWindowTitle("Miembros del grupo")
    dlg.setModal(True)
    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel("Selecciona entre 2 y 6 bots:"))
    listw = QListWidget(dlg)
    listw.setSelectionMode(QListWidget.SelectionMode.NoSelection)
    for b in bots:
        item = QListWidgetItem(f"@{b.get('slug', '')} — {b.get('display_name', '')}")
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, b.get("slug", ""))
        listw.addItem(item)
    layout.addWidget(listw)
    ok_btn = QPushButton("Crear grupo")
    ok_btn.setEnabled(False)
    cancel_btn = QPushButton("Cancelar")

    def _sync_ok() -> None:
        checked = sum(
            1 for i in range(listw.count()) if listw.item(i).checkState() == Qt.CheckState.Checked
        )
        ok_btn.setEnabled(2 <= checked <= 6)

    listw.itemChanged.connect(lambda *_: _sync_ok())
    ok_btn.clicked.connect(dlg.accept)
    cancel_btn.clicked.connect(dlg.reject)
    btns = QHBoxLayout()
    btns.addStretch(1)
    btns.addWidget(cancel_btn)
    btns.addWidget(ok_btn)
    layout.addLayout(btns)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return [
        listw.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(listw.count())
        if listw.item(i).checkState() == Qt.CheckState.Checked
    ]


def _fmt_local_schedule(nxt) -> str:
    """Naive-UTC de BD → reloj del usuario (los horarios son del sistema)."""
    from datetime import UTC, datetime

    try:
        dt = nxt if isinstance(nxt, datetime) else datetime.fromisoformat(str(nxt))
        return dt.replace(tzinfo=UTC).astimezone().strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        return str(nxt)[:16]


def _ask_text(parent: QWidget, title: str, label: str) -> str | None:
    from PySide6.QtWidgets import QInputDialog

    text, ok = QInputDialog.getText(parent, title, label)
    if not ok or not text.strip():
        return None
    return text.strip()
