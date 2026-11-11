"""Helper compartido para subprocesos asíncronos: timeout con kill del árbol."""

import asyncio
import os
import signal


def _kill_process_tree(proc) -> None:
    """Mata el grupo del hijo SOLO si abrió sesión propia (start_new_session).

    Si el hijo comparte el pgid del proceso actual, matar el grupo mataría a
    la app/shell: en ese caso se mata solo el hijo. El grupo <= 1 nunca es
    objetivo válido: killpg(1) equivale a kill(-1) — SIGKILL a todo lo
    señalizable por el usuario (regresión: un mock sin pid real resolvía
    getpgid→1 y tumbaba la sesión completa).
    """
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError, TypeError, ValueError):
        pgid = None
    if pgid is not None and pgid > 1 and pgid != os.getpgrp():
        try:
            os.killpg(pgid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass


async def communicate_or_kill(proc, timeout: float, input: bytes | None = None):
    """Espera proc.communicate(input); al timeout/cancelación mata el árbol."""
    try:
        return await asyncio.wait_for(proc.communicate(input=input), timeout=timeout)
    except asyncio.CancelledError:
        _kill_process_tree(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            pass
        raise
    except TimeoutError:
        _kill_process_tree(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            proc.terminate()
        raise
