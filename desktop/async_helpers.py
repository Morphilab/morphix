"""Helpers para integración asyncio + Qt — seguro ante errores silenciosos."""

import asyncio
import logging

logger = logging.getLogger(__name__)


def run_async(coro, loop=None):
    """Ejecuta una corrutina en el event loop de forma segura.

    A diferencia de asyncio.run_coroutine_threadsafe(), este helper
    registra un callback de error para que las excepciones no se pierdan
    silenciosamente.
    """
    try:
        loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        loop = loop or asyncio.get_event_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)

    def _log_error(fut):
        exc = fut.exception()
        if exc is None:
            return
        if isinstance(exc, asyncio.CancelledError):
            pass
        else:
            logger.error("Error en corrutina de fondo (Qt→asyncio): %s", exc)

    future.add_done_callback(_log_error)
    return future
