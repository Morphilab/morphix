# tests/test_async_helpers_guards.py — F2
"""El callback de run_async no debe reventar con futures cancelados.

fut.exception() sobre un concurrent.futures.Future cancelado lanza
concurrent.futures._base.CancelledError — NO la builtin asyncio.CancelledError
(clases distintas desde Python 3.8). El primer fix capturó la equivocada y el
ERROR 'exception calling callback' siguió apareciendo en el shutdown."""

import concurrent.futures
import logging

from desktop.async_helpers import run_async


def test_callback_sobrevive_future_cancelado(caplog):
    f = concurrent.futures.Future()
    f.cancel()
    coro_huerto = None  # run_async necesita un coro; probamos el callback directo

    # reproducir el wiring exacto: registrar el callback que run_async instala
    # llamando a run_async con un coro real y cancelando su future
    import asyncio

    async def _noop():
        pass

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        fut = run_async(_noop(), loop=loop)
        fut.cancel()
        with caplog.at_level(logging.ERROR):
            loop.run_until_complete(asyncio.sleep(0.01))
            loop.run_until_complete(asyncio.sleep(0.01))
    finally:
        loop.close()
    assert not any(
        "exception calling callback" in r.message or "CancelledError" in r.message
        for r in caplog.records
    ), "el callback de un future cancelado no debe loguear ERROR"
    assert coro_huerto is None
