# features/maestro/services/diagram_manager.py
"""
Diagram Manager — gestión de estado del workflow en vivo.
Uses StatusRenderer (HTML) instead of Mermaid. No external dependencies.
"""

import asyncio
import logging
from typing import Any

from orchestration.status import render as render_status
from orchestration.status import save_status_snapshot

logger = logging.getLogger(__name__)


async def update_live_diagram(G: Any, events: Any) -> str | None:
    """Actualiza el estado en vivo disparando eventos.

    Args:
        G: Grafo NetworkX del workflow (None = sin diagrama).
        events: WorkflowEvents con on_diagram_update y on_ui_refresh.

    Returns:
        El HTML generado, o None si no hay diagrama.
    """
    try:
        if G is None:
            logger.debug("Modo conversación simple: diagrama omitido (G=None)")
            return None

        if events is None:
            logger.warning("events es None, no se puede actualizar diagrama")
            return None

        logger.debug(
            "Actualizando diagrama - Nodos: %d | Estados: %s",
            len(G.nodes),
            [G.nodes[n].get("status", "pending") for n in G.nodes],
        )

        html = render_status(G)
        # Persist the snapshot off the event loop to avoid blocking the UI pump.
        await asyncio.to_thread(save_status_snapshot, html)

        if events.on_diagram_update is not None:
            await events.on_diagram_update(html, G)

        if events.on_ui_refresh is not None:
            await events.on_ui_refresh()

        logger.debug("✅ Diagrama actualizado correctamente")
        return html

    except Exception as e:
        logger.error("Error crítico actualizando diagrama: %s", e, exc_info=True)
        return None
