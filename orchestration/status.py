"""Status Renderer — genera HTML progresivo del workflow.

Replaces mermaid_helper.py. No external dependencies, no HTTP, no remote rendering.
El HTML se muestra en QTextBrowser (desktop) o como tabla Rich (CLI usa el grafo directamente).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from core.path_resolver import paths

logger = logging.getLogger(__name__)

STATUS_ICONS = {
    "completed": "🟢",
    "running": "🔵",
    "failed": "🔴",
    "pending": "⚫",
    "recovered": "🟡",
}
STATUS_COLORS = {
    "completed": "#22C55E",
    "running": "#3B82F6",
    "failed": "#EF4444",
    "pending": "#6B7280",
    "recovered": "#F59E0B",
}

HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="background:#0F0F0F; font-family:-apple-system,sans-serif; margin:0; padding:8px">
{cards}
</body></html>"""

CARD_TEMPLATE = """
<div style="background:#1A1A1A; border-left:3px solid {color};
            margin:4px 0; padding:8px 12px; border-radius:4px">
  <div style="color:{color}; font-size:13px; margin-bottom:4px">
    {icon} <b>{status_label}</b>
  </div>
  <div style="color:#E5E5E5; font-size:12px; line-height:1.4">{task}</div>
  <div style="color:#888; font-size:10px; margin-top:2px">Agente: {agent}</div>
</div>"""


def render(G: Any) -> str:
    """Genera HTML con tarjetas de estado para cada subtarea del workflow.

    Args:
        G: Grafo NetworkX con nodos que tienen 'task', 'agent', 'status'.

    Returns:
        String HTML listo para QTextBrowser.setHtml() o consola.
    """
    if G is None or G.number_of_nodes() == 0:
        return "<p style='color:#888; text-align:center'>Workflow vacío</p>"

    cards: list[str] = []
    for node in G.nodes():
        data = G.nodes[node]
        task = _clean_text(str(data.get("task", f"Subtarea {node}")), max_len=120)
        agent = str(data.get("agent", "?"))
        status = str(data.get("status", "pending"))

        icon = STATUS_ICONS.get(status, "⚫")
        color = STATUS_COLORS.get(status, "#888")
        status_label = status.upper()

        cards.append(
            CARD_TEMPLATE.format(
                color=color, icon=icon, status_label=status_label, task=task, agent=agent
            )
        )

    return HTML_TEMPLATE.format(cards="".join(cards))


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


def save_status_snapshot(html: str, filename: str | None = None) -> Path:
    """Guarda el HTML del workflow en charts/ como respaldo."""
    if filename is None:
        filename = f"workflow_{os.getpid()}_{int(time.time())}.html"
    path = paths.charts_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    logger.debug("Workflow status guardado: %s", path)
    return path
