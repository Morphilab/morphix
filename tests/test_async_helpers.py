# tests/test_async_helpers.py
"""run_async autocura la ausencia de event loop.

Mecanismo del polluter de la suite GUI: cualquier test que usa
`asyncio.run()` deja el thread-local de la policy en set_event_loop(None) +
_set_called=True; el siguiente test que dispara run_async muere con
"RuntimeError: There is no current event loop in thread 'MainThread'" (Python
3.12 ya no auto-crea el loop en esa condición). Eso rompe en CASCADA los
tests de switch_project/launch_workflow/bots/GUI-routing SOLO cuando corren
tras un test con asyncio.run.

La autocura sola no basta en la GUI real: el loop mintado no lo bombea
nadie (run.py solo bombea su loop con QTimer) y cada corrutina enmudece.
Con set_app_loop registrado, el destino es siempre el loop bombeado.
"""

import asyncio
import logging


def test_run_async_autocrea_loop_tras_asyncio_run():
    """Reproduce el envenenamiento de asyncio.run y exige resiliencia."""
    from desktop.async_helpers import run_async, set_app_loop

    set_app_loop(None)  # el autocurado exige ausencia de registro

    async def _noop():
        return 42

    asyncio.run(asyncio.sleep(0))  # envenena: _set_called=True, loop=None

    fut = run_async(_noop())
    assert fut is not None
    assert not fut.cancelled()

    # el loop autocreado es conduible y entrega el valor
    loop = asyncio.get_event_loop()
    assert loop.run_until_complete(asyncio.wrap_future(fut, loop=loop)) == 42
    loop.close()
    asyncio.set_event_loop(None)
    set_app_loop(None)


def test_run_async_funciona_con_loop_corriendo():
    """Caso sano: con loop corriendo (asyncio.run) agenda y resuelve."""
    from desktop.async_helpers import run_async

    async def _main():
        async def _val():
            return 7

        fut = run_async(_val())
        # dentro del loop corriendo: la corrutina se ejecuta por el driver
        return await asyncio.wrap_future(fut)

    assert asyncio.run(_main()) == 7


def test_run_async_prefiere_el_loop_de_app_registrado():
    """Repro del envenenamiento real de la GUI: asyncio.run en el hilo Qt
    deja el thread-local sin loop; con el loop de la app registrado la
    corrutina DEBE caer en ese loop (el único bombeado), no en uno huérfano."""
    from desktop.async_helpers import run_async, set_app_loop

    app_loop = asyncio.new_event_loop()
    set_app_loop(app_loop)
    try:
        asyncio.run(asyncio.sleep(0))  # envenena el thread-local

        async def _where():
            return asyncio.get_running_loop()

        fut = run_async(_where())
        assert not fut.done()  # aún sin bombear
        # loop explícito: el thread-local sigue envenenado a propósito
        ran_on = app_loop.run_until_complete(asyncio.wrap_future(fut, loop=app_loop))
        assert ran_on is app_loop
    finally:
        set_app_loop(None)
        app_loop.close()


def test_run_async_ultimo_recurso_avisa_loud(caplog):
    """Sin loop de app registrado, mintar un loop local debe ser ruidoso:
    un loop sin dueño que nadie bombea es un no-op silencioso."""
    from desktop.async_helpers import run_async, set_app_loop

    set_app_loop(None)

    async def _val():
        return 3

    with caplog.at_level(logging.WARNING, logger="desktop.async_helpers"):
        asyncio.run(asyncio.sleep(0))  # envenena
        fut = run_async(_val())
        assert fut is not None
    assert "sin loop de app registrado" in caplog.text
    # conduce y cierra el loop mintado para no filtrar sockets/corrutinas
    minted = asyncio.get_event_loop()
    minted.run_until_complete(asyncio.wrap_future(fut, loop=minted))
    minted.close()
    asyncio.set_event_loop(None)
    set_app_loop(None)
