"""Daemon headless: rutinas y pending_turns sin GUI (v1).

Alcance v1 (opción (a) del plan de la fase): el tick de rutinas y el wake de
``pending_turns`` — la maquinaria de BD ya es multi-proceso segura (advisory
lock del scheduler + SKIP LOCKED del wake). NO hospeda todavía una cola de
workflows autónomos (no existe planificador), ni el indexado como servicio, ni
el MCP server: esos son v2/v3.

Reglas:
- Single-instance: advisory lock dedicado tomado en una conexión que vive todo
  el proceso; un segundo arranque sale limpio (exit 0).
- El daemon JAMÁS llama ``switch_workspace``: trabaja en el workspace del
  bootstrap (``bound_schema`` por run). Cambiar el schema global desde un
  proceso de fondo corrompería runs concurrentes de la GUI.
- SIGTERM/SIGINT: cancela los loops, ``stop_daemons()``, libera el lock y
  cierra el pool. El journal parcial de cada run es la durabilidad.
- ``--once`` corre un tick de cada loop y sale (cron/systemd timer).
- ``--dry-run`` bootstrapea y reporta sin ejecutar ticks (smoke sin efectos).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Espacio propio del daemon; ≠ 917231001 del scheduler de rutinas (ese lock
# protege un tick, este protege la INSTANCIA del daemon).
_INSTANCE_LOCK_KEY = 917231002


def setup_logging(log_path: str | None = None) -> Path:
    """Log propio del daemon (nunca el terminal de la GUI)."""
    target = Path(log_path or "logs/daemon.log")
    target.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        handlers=[logging.FileHandler(target, encoding="utf-8")],
        force=True,
    )
    return target


async def _acquire_instance_lock():
    """Advisory lock de instancia; None si otro daemon ya corre."""
    from sqlalchemy import text

    from core.database import _get_async_engine

    engine = _get_async_engine()
    conn = await engine.connect()
    got = (await conn.execute(text(f"SELECT pg_try_advisory_lock({_INSTANCE_LOCK_KEY})"))).scalar()
    if not got:
        await conn.close()
        return None
    return conn


async def _release_instance_lock(conn) -> None:
    from sqlalchemy import text

    try:
        await conn.execute(text(f"SELECT pg_advisory_unlock({_INSTANCE_LOCK_KEY})"))
    finally:
        await conn.close()


async def run_daemon(
    *,
    workspace: str | None = None,
    once: bool = False,
    max_seconds: float | None = None,
    dry_run: bool = False,
) -> int:
    """Arranca el daemon. Devuelve el exit code (0 = ok / ya había instancia)."""
    from core.bootstrap import init_backend, register_daemon, start_daemons, stop_daemons
    from core.database import dispose_engine
    from tools.loader import load_global_tools

    ok = await init_backend(workspace)
    if not ok:
        logger.error("init_backend falló; daemon no arranca")
        return 1
    load_global_tools()

    lock = await _acquire_instance_lock()
    if lock is None:
        logger.info("otro daemon ya corre (advisory lock); salida limpia")
        await dispose_engine()
        return 0

    from orchestration.bots_clock import execute_prompt, routines_loop
    from orchestration.bots_wake import bots_wake_loop, drain_tick

    try:
        if dry_run:
            logger.info("dry-run: bootstrap OK; ticks NO ejecutados")
            return 0

        if once:
            from core import bots_routines

            wake = await drain_tick()
            tick = await bots_routines.scheduler_tick(run_prompt=execute_prompt)
            logger.info("once: wake=%s rutinas=%s", wake, tick)
            return 0

        await start_daemons()
        register_daemon(bots_wake_loop())
        register_daemon(routines_loop())
        logger.info("daemon ON (workspace=%s)", workspace or "activo")

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass

        if max_seconds is not None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=max_seconds)
            except TimeoutError:
                pass
        else:
            await stop.wait()
        logger.info("señal de parada; cerrando")
        return 0
    finally:
        try:
            await stop_daemons()
        except Exception:
            logger.warning("stop_daemons falló", exc_info=True)
        await _release_instance_lock(lock)
        try:
            await dispose_engine()
        except Exception:
            logger.warning("dispose_engine falló", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.daemon")
    ap.add_argument("--workspace", default=None, help="workspace del bootstrap (default: activo)")
    ap.add_argument("--once", action="store_true", help="un tick de wake+rutinas y sale")
    ap.add_argument("--max-seconds", type=float, default=None, help="parada automática (tests)")
    ap.add_argument("--dry-run", action="store_true", help="bootstrap + lock, sin ticks")
    ap.add_argument("--log", default="logs/daemon.log", help="archivo de log propio")
    args = ap.parse_args(argv)

    setup_logging(args.log)
    try:
        return asyncio.run(
            run_daemon(
                workspace=args.workspace,
                once=args.once,
                max_seconds=args.max_seconds,
                dry_run=args.dry_run,
            )
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
