# core/environments/base.py — contrato de backends de ejecución
"""Interfaz de los entornos de ejecución + teardown escalonado.

Diseño propio, sin duplicar lo que ya existe:

- ``core/utils.build_child_env`` YA es el env-allowlist anti-secretos
  (regla "prohibido os.environ.copy()"): los backends lo
  REUSAN, no lo reinventan.
- ``tools/bash_manager`` ya corre con grupo propio (``start_new_session``)
  y kill de grupo; lo que aporta este paquete es la ESCALADA GRACIOUS
  (SIGTERM → grace → SIGKILL — en versión grupo-de-procesos) y la matriz
  PLUGGABLE: tabla de backends,
  nunca ``elif`` sobre el nombre (regla de forma del repo).

v1: contrato de corrida ONE-SHOT (``run`` → :class:`ExecResult`), que es
lo que todo consumidor actual necesita (bash, runners de trayectorias, delegación).
Procesos long-lived de bash_manager siguen su propio camino.

``ssh``/``modal``/``daytona`` quedan como proveedores futuros: la tabla
los aceptará sin tocar esta base.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from core.output_bounds import TOOL_OUTPUT_MAX_BYTES, bound_output

logger = logging.getLogger(__name__)

MAX_READ_BYTES = 2 * 1024 * 1024


@dataclass
class ExecResult:
    """Resultado uniforme de una corrida, formato bash_manager."""

    exit_code: int
    output: str
    success: bool
    timed_out: bool = False
    backend: str = ""

    @staticmethod
    def capped(stdout: str, stderr: str = "") -> str:
        """Salida unida + bounding byte-exacto con cola preservada."""
        parts = [p for p in (stdout.strip(), f"[stderr]\n{stderr.strip()}" if stderr else "") if p]
        text = "\n".join(parts).strip() or "(no output)"
        return bound_output(text, TOOL_OUTPUT_MAX_BYTES, tail_bytes=2_000).text


class BaseEnvironment(ABC):
    """Contrato de un backend de ejecución."""

    name: str = ""

    @abstractmethod
    async def run(
        self,
        command: str | list[str],
        *,
        cwd: str | Path,
        timeout: float = 30.0,
        env_extra: dict[str, str] | None = None,
        home: str | None = None,
    ) -> ExecResult:
        """Corre un comando one-shot y devuelve el resultado uniforme."""

    async def read_file(self, path: str | Path, *, max_bytes: int = MAX_READ_BYTES) -> bytes:
        """Lee un fichero del entorno con tope de tamaño."""
        data = Path(path).read_bytes()
        return data[:max_bytes]

    async def write_file(self, path: str | Path, data: bytes, *, workspace: str) -> dict:
        """Escribe a través del fs_fence — jamás fuera de raíces escribibles."""
        from core.fs_fence import check_write_target

        ok, reason = check_write_target(str(path), workspace)
        if not ok:
            return {"success": False, "error": reason}
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"success": True, "path": str(target)}


# ── Registro por tabla (regla de forma: jamás elif sobre el backend) ────────

BACKENDS: dict[str, type[BaseEnvironment]] = {}


def register_backend(cls: type[BaseEnvironment]) -> type[BaseEnvironment]:
    """Decorador de alta: la tabla es la única fuente de verdad."""
    if not cls.name:
        raise ValueError(f"backend {cls.__name__} sin 'name'")
    BACKENDS[cls.name] = cls
    return cls


def available_backends() -> list[str]:
    return sorted(BACKENDS)


def create_environment(name: str | None = None, **kwargs) -> BaseEnvironment:
    """Factoría: resuelve por tabla con error accionable (lista incluida)."""
    from core.config import settings

    backend = name or settings.execution_default_backend
    cls = BACKENDS.get(backend)
    if cls is None:
        raise ValueError(
            f"backend de ejecución desconocido: '{backend}'. "
            f"Usa uno de: {', '.join(available_backends())}"
        )
    return cls(**kwargs)


# ── Teardown escalonado (versión grupo) ─────────────────────────────────


async def terminate_group_graceful(
    proc: asyncio.subprocess.Process, *, grace_s: float = 5.0
) -> None:
    """SIGTERM al grupo → espera grace → SIGKILL al grupo.

    El contrato: primero la señal GRACIOUS (un supervisor con trap
    reapa a sus hijos y muere limpio); el SIGKILL solo para quien ignore
    la primera. Señalar el GRUPO completo evita el orden padre/hijos de
    la versión por-pid sin perder contención (start_new_session aísla).
    """
    if proc.returncode is not None:
        return  # ya murió
    _signal_group(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=max(0.1, grace_s))
        return
    except TimeoutError:
        pass
    _signal_group(proc, signal.SIGKILL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except TimeoutError:
        logger.warning("grupo %s sobrevivió al SIGKILL — posible zombie", getattr(proc, "pid", "?"))


def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError, TypeError, ValueError):
        pgid = None
    if pgid is not None and pgid > 1 and pgid != os.getpgrp():
        try:
            os.killpg(pgid, sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.send_signal(sig)
    except (ProcessLookupError, OSError):
        pass  # ya muerto: sin drama
