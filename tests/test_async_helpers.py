# tests/test_async_helpers.py
"""run_async autocura la ausencia de event loop.

Mecanismo del polluter de la suite GUI: cualquier test que usa
`asyncio.run()` deja el thread-local de la policy en set_event_loop(None) +
_set_called=True; el siguiente test que dispara run_async muere con
"RuntimeError: There is no current event loop in thread 'MainThread'" (Python
3.12 ya no auto-crea el loop en esa condición). Eso rompe en CASCADA los
tests de switch_project/launch_workflow/bots/GUI-routing SOLO cuando corren
tras un test con asyncio.run.
"""

import asyncio


def test_run_async_autocrea_loop_tras_asyncio_run():
    """Reproduce el envenenamiento de asyncio.run y exige resiliencia."""
    from desktop.async_helpers import run_async

    async def _noop():
        return 42

    asyncio.run(asyncio.sleep(0))  # envenena: _set_called=True, loop=None

    fut = run_async(_noop())
    assert fut is not None
    assert not fut.cancelled()

    # el loop autocreado es conduible y entrega el valor
    loop = asyncio.get_event_loop()
    assert loop.run_until_complete(asyncio.wrap_future(fut)) == 42


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
