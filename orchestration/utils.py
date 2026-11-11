"""
Workflow Utils - Funciones compartidas de limpieza de salida
"""

import logging

logger = logging.getLogger(__name__)


async def apply_undercover(text: str) -> str:
    """Aplica anti-distillation/watermark de forma uniforme a las salidas.

    Todas las rutas que entregan texto al usuario (workflows DSL, comando
    directo, chat de agente y bots) pasan por este helper.
    """
    from core.security.undercover_mode import undercover

    return await undercover.get_safe_response_async(text)
