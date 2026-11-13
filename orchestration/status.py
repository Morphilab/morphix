"""Status Renderer — genera HTML progresivo del workflow.

Replaces mermaid_helper.py. No external dependencies, no HTTP, no remote rendering.
El HTML se muestra en QTextBrowser (desktop) o como tabla Rich (CLI usa el grafo directamente).
"""

from __future__ import annotations

import logging

from core.path_resolver import paths

logger = logging.getLogger(__name__)

# Prefijos (case-insensitive, anclados AL INICIO del status) del contrato de
# stats que indican que el run YA NO está en ejecución. Los emits terminales
# empiezan con la palabra ("Completado", "Fallido", "Detenido"...), mientras
# que los intermedios solo la contienen en medio ("Ronda 2/3 completada",
# "Aggregating (2 done, 1 failed)", "DAG level completed"). Vive aquí (no en
# la GUI) porque el emitter también lo necesita para no dejar que un emit
# diferido pise el cierre del run.
TERMINAL_STATUS_PREFIXES = (
    "completad",  # Completado/Completada (final español; diverge de "completed")
    "complete",  # completed (final inglés: pipeline y agent loop)
    "fallid",  # Fallido/Fallida (ruta TDD fallida)
    "detenid",  # Detenido/Detenida (run stopped por plan inconsistente)
    "cancel",  # Cancelado/cancelled
    "paus",  # Pausa/paused (gate/checkpoint de pipeline)
    "fail",  # failed (stage/gate de pipeline)
    "error",  # Error ... (defensivo; sin emits actuales)
    "idle",  # default del WorkflowEmitter
)


def is_terminal_status(status: object) -> bool:
    """True si el status de stats representa el FIN del run (no un parcial)."""
    text = str(status or "").strip().lower()
    return bool(text) and text.startswith(TERMINAL_STATUS_PREFIXES)


STATUS_ICONS = {
    "completed": "🟢",
    "delivered": "🟢",
    "running": "🔵",
    "failed": "🔴",
    "pending": "⚫",
    "recovered": "🟡",
    "unmeasured": "🟠",
    "blocked": "⏳",
    "skipped": "⏭️",
}
STATUS_COLORS = {
    "completed": "#22C55E",
    "delivered": "#22C55E",
    "running": "#3B82F6",
    "failed": "#EF4444",
    "pending": "#6B7280",
    "recovered": "#F59E0B",
    "unmeasured": "#F97316",
    "blocked": "#A855F7",
    "skipped": "#64748B",
}
DEFAULT_CARD_COLORS = {
    "card_bg": "#1A1A1A",
    "body_bg": "#0F0F0F",
    "text": "#E5E5E5",
    "dim": "#888",
    "accent": "#F59E0B",
}

HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="background:{body_bg}; font-family:-apple-system,sans-serif; margin:0; padding:8px">
{cards}
</body></html>"""

CARD_TEMPLATE = """
<div style="background:{card_bg}; border-left:3px solid {color};
            margin:4px 0; padding:8px 12px; border-radius:4px">
  <div style="color:{color}; font-size:13px; margin-bottom:4px">
    {icon} <b>{status_label}</b>
  </div>
  <div style="color:{text}; font-size:12px; line-height:1.4">{task}</div>
  <div style="color:{dim}; font-size:10px; margin-top:2px">Agente: {agent}</div>
</div>"""


def _merged_colors(colors: dict | None) -> dict:
    merged = {**STATUS_COLORS, **DEFAULT_CARD_COLORS}
    if colors:
        merged.update(colors)
    return merged


def _clean_text(text: str, max_len: int = 100) -> str:
    """Sanitiza texto para HTML: escapa caracteres especiales y trunca."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace('"', "&quot;").replace("\n", " ").replace("\r", "").replace("\t", " ")
    if len(text) > max_len:
        last_space = text[:max_len].rfind(" ")
        if last_space > max_len // 2:
            text = text[:last_space] + "..."
        else:
            text = text[: max_len - 3] + "..."
    return text.strip() or "Sin descripción"


_SNAPSHOT_RETENTION = 20


def _prune_old_snapshots(keep: int = _SNAPSHOT_RETENTION) -> int:
    """Retención — deja solo los `keep` snapshots más recientes."""
    try:
        snapshots = sorted(
            paths.charts_dir().glob("workflow_*.html"), key=lambda p: p.stat().st_mtime
        )
        removed = 0
        for old in snapshots[:-keep]:
            old.unlink(missing_ok=True)
            removed += 1
        return removed
    except Exception:
        logger.debug("Pruning de snapshots falló", exc_info=True)
        return 0


def render_from_subtasks(
    subtask_list: list[dict],
    phase: str | None = None,
    active_phase: str | None = None,
    colors: dict | None = None,
) -> str:
    """Genera HTML de tarjetas de estado agrupadas por fase (spec §3.4).

    Deriva exclusivamente de subtask_list (el mismo dato que la lista de
    Subtareas de la UI) — cero drift entre ambas vistas. La agrupación por
    fase se muestra como encabezado; si no hay fase, lista plana. Un ítem
    puede declarar su propia ``phase`` (la GUI la deriva del plan del run):
    se abre un encabezado cada vez que cambia, en el orden del plan.
    colors: overrides opcionales de color ({status_key|card_key: color}).
    """
    if not subtask_list:
        return "<p style='color:#888; text-align:center'>Workflow vacío</p>"

    palette = _merged_colors(colors)

    phase_header = ""
    if phase:
        phase_header = (
            f"<div style='color:{palette['accent']}; font-size:12px; font-weight:bold; "
            f"margin:10px 0 4px 0'>📂 {_clean_text(phase, 60)}</div>"
        )

    cards: list[str] = []
    current_group: str | None = None
    for item in subtask_list:
        item_phase = str(item.get("phase") or "").strip()
        if item_phase and item_phase != current_group:
            current_group = item_phase
            cards.append(
                f"<div style='color:{palette['accent']}; font-size:11px; font-weight:bold; "
                f"margin:8px 0 2px 0'>📂 {_clean_text(item_phase, 60)}</div>"
            )
        name = item.get("name", "Subtarea")
        status = item.get("status", "pending")
        agent = item.get("agent") or "—"
        icon = STATUS_ICONS.get(status, "⚫")
        color = palette.get(status, "#888")
        cards.append(
            CARD_TEMPLATE.format(
                card_bg=palette["card_bg"],
                color=color,
                icon=icon,
                status_label=status.upper(),
                task=_clean_text(str(name), 120),
                agent=_clean_text(str(agent), 40),
                text=palette["text"],
                dim=palette["dim"],
            )
        )

    return HTML_TEMPLATE.format(body_bg=palette["body_bg"], cards=phase_header + "".join(cards))
