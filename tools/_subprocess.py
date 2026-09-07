"""Helper compartido para subprocesos asíncronos: timeout con kill garantizado."""

import asyncio


async def communicate_or_kill(proc, timeout: float, input: bytes | None = None):
    """Espera proc.communicate(input) con timeout; mata el proceso si expira.

    Si el timeout se alcanza, envía SIGKILL y espera la terminación antes
    de re-lanzar TimeoutError — evita procesos huérfanos acumulados.
    """
    try:
        return await asyncio.wait_for(proc.communicate(input=input), timeout=timeout)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            proc.terminate()
        raise
