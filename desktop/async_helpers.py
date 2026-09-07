"""Helpers para integración asyncio + Qt — seguro ante errores silenciosos."""

import asyncio
import logging
from concurrent.futures import CancelledError as _FuturesCancelledError

logger = logging.getLogger(__name__)


def run_async(coro, loop=None):
    """Ejecuta una corrutina en el event loop de forma segura.

    A diferencia de asyncio.run_coroutine_threadsafe(), este helper
    registra un callback de error para que las excepciones no se pierdan
    silenciosamente.

    Autocuración del thread-local de asyncio: `asyncio.run()`
    deja la policy en set_event_loop(None) + _set_called=True; desde Python
    3.12 get_event_loop() NO auto-crea el loop en esa condición y lanzaba
    RuntimeError, envenenando a los siguientes llamadores de run_async.
    Si no hay loop disponible, se crea y registra uno nuevo.
    """
    try:
        loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        try:
            loop = loop or asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    future = asyncio.run_coroutine_threadsafe(coro, loop)

    def _log_error(fut):
        try:
            exc = fut.exception()
        except _FuturesCancelledError:
            # Shutdown race: el future se canceló antes del callback.
            # OJO: fut.exception() lanza concurrent.futures.CancelledError,
            # NO asyncio.CancelledError (builtin) — clases distintas desde
            # Python 3.8; capturar la equivocada reintroduce la pérdida
            # silenciosa del error.
            return
        if exc is None:
            return
        if isinstance(exc, asyncio.CancelledError):
            logger.warning("Background coroutine cancelled (Qt asyncio bridge)")
        else:
            logger.error("Error en corrutina de fondo (Qt→asyncio): %s", exc)

    future.add_done_callback(_log_error)
    return future
