# mypy: ignore-errors
"""EntityInfoDialog — ventana read-only con la info de la entidad activa.

Un solo diálogo, tres builders de contenido: for_workflow / for_agent /
for_bot. No-modal (consulta: no bloquea el Maestro — patrón events.py
raise_/activateWindow sin exec). Los datos vienen de las fuentes ya
existentes: load_workflow_view (services/workflow_view), agents_registry
y BotsService.get_bot (core/bots).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import COLORS, StyleFactory

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _W

# Labels amigables para los 10 kinds del DSL (orchestration/dsl/schema.py)
_KIND_LABELS = {
    "agent": "Agente",
    "tool": "Tool",
    "decompose": "Descomposición",
    "parallel": "Paralelo",
    "loop": "Bucle",
    "decide": "Decisión",
    "checkpoint": "Checkpoint humano",
    "workflow": "Sub-workflow",
    "plugin": "Plugin",
    "aggregate": "Agregación",
}

_ACRONYMS = {"tdd", "bdd", "sdd", "edd", "atdd", "dsl", "api", "ui"}


def _display_name(slug: str) -> str:
    words = []
    for word in slug.replace("_", " ").split():
        words.append(word.upper() if word in _ACRONYMS else word.capitalize())
    return " ".join(words)


class EntityInfoDialog(QDialog):
    """Info read-only de la entidad activa. Sin botones: se cierra con la X."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setMinimumSize(580, 500)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(self._scroll_style())
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(4, 4, 4, 4)
        self._body.setSpacing(10)
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll, 1)

    @staticmethod
    def _scroll_style() -> str:
        c = COLORS
        return (
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 5px; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: {c['border_light']}; "
            "border-radius: 2px; min-height: 30px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }"
            "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
        )

    # ── Secciones ──

    def _add_header(self, title: str, badge: str | None = None) -> None:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lbl = QLabel(title)
        lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {COLORS['text_primary']};")
        lay.addWidget(lbl)
        if badge:
            b = QLabel(badge)
            b.setStyleSheet(
                f"color: {COLORS['text_dim']}; font-size: 10px; "
                f"border: 1px solid {COLORS['border_light']}; border-radius: 999px; "
                "padding: 1px 8px;"
            )
            lay.addWidget(b)
        lay.addStretch()
        self._body.addWidget(row)

    def _add_section(self, title: str) -> QLabel:
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"font-size: 10px; letter-spacing: 1px; color: {COLORS['text_dim']}; "
            "font-weight: 600;"
        )
        self._body.addWidget(lbl)
        return lbl

    def _add_text(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-size: 12px; color: {COLORS['text_secondary']};")
        self._body.addWidget(lbl)

    def _add_flags(self, flags: list[tuple[str, bool]]) -> None:
        parts = [
            f"<span style='color:{COLORS[('success' if ok else 'text_dim')]}'>"
            f"{'✓' if ok else '✗'}</span> {name}"
            for name, ok in flags
        ]
        lbl = QLabel(" &nbsp;·&nbsp; ".join(parts))
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setStyleSheet(f"font-size: 12px; color: {COLORS['text_secondary']};")
        self._body.addWidget(lbl)

    def _add_mono(self, text: str, title: str | None = None, max_h: int = 240) -> None:
        if title:
            self._add_section(title)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        box.setMaximumHeight(max_h)
        box.setStyleSheet(StyleFactory.text_editor())
        self._body.addWidget(box)

    def _add_kinds(self, kind_summary: dict[str, int]) -> None:
        self._add_section("PASOS (DSL)")
        items = [
            f"<span style='color:{COLORS['text_primary']}'>{_KIND_LABELS.get(k, k)}" f"</span> ×{n}"
            for k, n in sorted(kind_summary.items(), key=lambda kv: -kv[1])
        ]
        lbl = QLabel(" &nbsp;·&nbsp; ".join(items))
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setStyleSheet(f"font-size: 12px; color: {COLORS['text_secondary']};")
        lbl.setWordWrap(True)
        self._body.addWidget(lbl)

    def _add_chips_row(self, title: str, items: list[str], empty: str) -> None:
        self._add_section(title)
        self._add_text(" &nbsp;·&nbsp; ".join(items) if items else empty)

    # ── Builders ──

    @classmethod
    def for_workflow(cls, parent: _W | None, workspace: str, name: str) -> EntityInfoDialog | None:
        from desktop.services.workflow_view import load_workflow_view

        view = load_workflow_view(workspace, name)
        if not view:
            return None
        dlg = cls(f"Workflow — {_display_name(name)}", parent)
        dlg._add_header(_display_name(name), "DSL" if view.get("dsl") else "legacy")
        desc = str(view.get("description", "")).strip()
        if desc:
            dlg._add_text(desc)
        dlg._add_chips_row(
            "EQUIPO (agents_allowed)",
            [str(a) for a in view.get("agents_allowed") or []],
            "Auto — el workflow reparte por subtarea",
        )
        dlg._add_chips_row(
            "TOOLS (tools_allowed)",
            [str(t) for t in view.get("tools_allowed") or []],
            "sin allowlist — tools por agente",
        )
        dlg._add_flags(
            [
                ("Requiere proyecto", bool(view.get("project_required"))),
                ("Skills", bool(view.get("skills"))),
            ]
        )
        if view.get("kind_summary"):
            dlg._add_kinds(view["kind_summary"])
        dlg._add_mono(
            yaml.safe_dump(view.get("raw") or {}, allow_unicode=True, sort_keys=False),
            title="YAML CRUDO",
        )
        return dlg

    @classmethod
    def for_agent(cls, parent: _W | None, name: str, profile: dict[str, Any]) -> EntityInfoDialog:
        dlg = cls(f"Agente — {name.capitalize()}", parent)
        dlg._add_header(name.capitalize(), str(profile.get("type") or "agente"))
        dlg._add_flags(
            [
                (f"Temperatura {profile.get('temperature', '—')}", True),
            ]
        )
        dlg._add_chips_row(
            "TOOLS",
            [str(t) for t in profile.get("tools") or []],
            "Ninguna",
        )
        keywords = [str(k) for k in profile.get("keywords") or []]
        if keywords:
            dlg._add_chips_row("KEYWORDS", keywords, "—")
        guidance = str(profile.get("length_guidance") or "").strip()
        if guidance:
            dlg._add_text(guidance)
        prompt = str(profile.get("system_prompt") or "").strip()
        dlg._add_mono(prompt or "(sin system_prompt)", title="SYSTEM PROMPT", max_h=280)
        return dlg

    @classmethod
    def for_bot(cls, parent: _W | None, bot: dict[str, Any]) -> EntityInfoDialog:
        display = str(bot.get("display_name") or bot.get("slug", "?"))
        enabled = bool(bot.get("enabled", True))
        dlg = cls(f"Bot — {display}", parent)
        dlg._add_header(
            display,
            f"@{bot.get('slug', '?')}" + ("" if enabled else " · deshabilitado"),
        )
        desc = str(bot.get("description") or "").strip()
        if desc:
            dlg._add_text(desc)
        dlg._add_text(
            f"Modelo: {bot.get('provider') or '—'}/{bot.get('model') or '—'}"
            f" &nbsp;·&nbsp; Temperatura: {bot.get('temperature', '—')}"
        )
        dlg._add_flags([("Habilitado", enabled)])
        dlg._add_chips_row("TOOLS", [str(t) for t in bot.get("tool_names") or []], "Ninguna")
        dlg._add_chips_row(
            "SKILLS", [str(s) for s in bot.get("skill_allowlist") or []], "Todas (sin allowlist)"
        )
        soul = str(bot.get("soul_md") or "").strip()
        dlg._add_mono(soul or "(sin SOUL)", title="SOUL", max_h=280)
        return dlg
