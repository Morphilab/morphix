# tests/test_async_blocking.py — run_async_blocking desde slots Qt
"""La espera bloqueante de corrutinas no puede envenenar el loop de la app.

Un slot Qt que necesita un valor async YA no puede usar asyncio.run: eso
deja el thread-local sin loop y las corridas siguientes enmudecen la GUI.
run_async_blocking espera el future con un QEventLoop anidado mientras el
pump (QTimer) sigue bombeando el loop registrado — mismo arreglo que run.py.
"""

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")

from desktop.async_helpers import run_async_blocking, set_app_loop  # noqa: E402


@pytest.fixture()
def pumped_loop():
    """QApplication offscreen + loop de la app bombeado por QTimer (como run.py)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    loop = asyncio.new_event_loop()
    set_app_loop(loop)

    def _pump():
        loop.call_soon(loop.stop)
        loop.run_forever()

    timer = QTimer()
    timer.timeout.connect(_pump)
    timer.start(5)
    try:
        yield loop
    finally:
        timer.stop()
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if pending:
            for t in pending:
                t.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        set_app_loop(None)
        loop.close()


def test_resuelve_valor_desde_contexto_sincrono(pumped_loop):
    async def _value():
        await asyncio.sleep(0.02)
        return 42

    assert run_async_blocking(_value(), timeout_s=5) == 42


def test_propaga_la_excepcion_de_la_corrutina(pumped_loop):
    async def _boom():
        raise ValueError("sin roster")

    with pytest.raises(ValueError, match="sin roster"):
        run_async_blocking(_boom(), timeout_s=5)


def test_timeout_levanta_timeouterror(pumped_loop):
    async def _hang():
        await asyncio.sleep(30)

    with pytest.raises(TimeoutError):
        run_async_blocking(_hang(), timeout_s=0.2)


def test_rechaza_llamada_desde_dentro_de_corrutina(pumped_loop):
    loop = pumped_loop

    async def _quick():
        return 1

    async def _bad():
        coro = _quick()
        try:
            return run_async_blocking(coro, timeout_s=1)
        finally:
            coro.close()  # el guard rechaza antes de agendarla

    fut = asyncio.run_coroutine_threadsafe(_bad(), loop)
    with pytest.raises(RuntimeError, match="corrutina"):
        loop.run_until_complete(asyncio.wrap_future(fut, loop=loop))
