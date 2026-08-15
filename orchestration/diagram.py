# orchestration/diagram.py
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
    """Actualiza el estado en vivo del workflow.

    Ya NO emite por events (el signal diagram_update se eliminó de la frontera,
    spec §3.4): la UI deriva el diagrama localmente de cada stats_update.
    Conserva la construcción del grafo y el snapshot a charts/.

    Args:
        G: Grafo NetworkX del workflow (None = sin diagrama).
        events: WorkflowEvents (se conserva por compatibilidad de firma).

    Returns:
        El HTML generado, o None si no hay diagrama.
    """
    try:
        if G is None:
            logger.debug("Modo conversación simple: diagrama omitido (G=None)")
            return None

        logger.debug(
            "Actualizando diagrama - Nodos: %d | Estados: %s",
            len(G.nodes),
            [G.nodes[n].get("status", "pending") for n in G.nodes],
        )

        html = render_status(G)
        # Persist the snapshot off the event loop to avoid blocking the UI pump.
        await asyncio.to_thread(save_status_snapshot, html)

        if events is not None and events.on_ui_refresh is not None:
            await events.on_ui_refresh()

        logger.debug("✅ Diagrama actualizado correctamente")
        return html

    except Exception as e:
        logger.error("Error crítico actualizando diagrama: %s", e, exc_info=True)
        return None
