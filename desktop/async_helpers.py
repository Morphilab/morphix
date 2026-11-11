"""Helpers para integración asyncio + Qt — seguro ante errores silenciosos."""

import asyncio
import logging
from concurrent.futures import CancelledError as _FuturesCancelledError

logger = logging.getLogger(__name__)

# Loop de la app registrado por el arranque (run.py): el ÚNICO que alguien
# bombea (QTimer). Si el thread-local queda envenenado por un asyncio.run,
# run_async debe seguir apuntando aquí — mintar un loop nuevo sin dueño
# convierte cada llamada en un no-op silencioso.
_app_loop: asyncio.AbstractEventLoop | None = None


def set_app_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Registra el loop bombeado por la app; None desregistra (tests)."""
    global _app_loop
    _app_loop = loop


def get_app_loop() -> asyncio.AbstractEventLoop | None:
    return _app_loop


def _resolve_loop(loop=None):
    """Loop objetivo: argumento > running > loop de la app > thread-local >
    último recurso mintar uno nuevo (con warning: un loop sin dueño que
    nadie bombea es un agujero negro silencioso)."""
    if loop is not None:
        return loop
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass
    if _app_loop is not None and not _app_loop.is_closed():
        return _app_loop
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        logger.warning(
            "run_async sin loop de app registrado: se creó un loop local que "
            "nadie bombea — conduce el future o registra el loop con set_app_loop"
        )
        return new_loop


def run_async(coro, loop=None):
    """Ejecuta una corrutina en el event loop de forma segura.

    A diferencia de asyncio.run_coroutine_threadsafe(), este helper
    registra un callback de error para que las excepciones no se pierdan
    silenciosamente.

    Autocuración del thread-local de asyncio: `asyncio.run()`
    deja la policy en set_event_loop(None) + _set_called=True; desde Python
    3.12 get_event_loop() NO auto-crea el loop en esa condición y lanzaba
    RuntimeError, envenenando a los siguientes llamadores de run_async.
    Con el loop de la app registrado (set_app_loop) el destino es siempre
    el loop bombeado; sin registro, se crea uno nuevo avisando loud.
    """
    future = asyncio.run_coroutine_threadsafe(coro, _resolve_loop(loop))

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


def run_async_blocking(coro, timeout_s: float = 10.0):
    """Espera el resultado de una corrutina desde contexto síncrono de Qt.

    run_async es fire-and-forget: su future no resuelve si nadie bombea el
    loop. Este helper espera con un QEventLoop anidado — el QTimer que bombea
    el loop de la app sigue disparándose durante la espera y el future
    resuelve. Jamás llamar desde dentro de una corrutina: re-entrar
    run_forever sobre el loop en curso rompe el pump (guard de entrada).

    Raises:
        TimeoutError: el future no resolvió en ``timeout_s``.
        RuntimeError: invocado con un loop corriendo en el hilo actual.
    """
    from PySide6.QtCore import QEventLoop, QTimer

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "run_async_blocking no puede llamarse desde dentro de una "
            "corrutina (re-entrar el event loop en curso); usa await"
        )

    future = run_async(coro)
    waiter = QEventLoop()
    future.add_done_callback(lambda _fut: waiter.quit())

    def _bail() -> None:
        future.cancel()
        waiter.quit()

    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(_bail)
    timeout.start(int(timeout_s * 1000))
    try:
        waiter.exec()
    finally:
        timeout.stop()
    if future.cancelled() or not future.done():
        raise TimeoutError(f"la corrutina no resolvió en {timeout_s}s")
    return future.result()
