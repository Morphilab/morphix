# core/sandbox/restricted_executor.py — supervisor del sandbox confinado
"""Ejecuta código del modelo en un PROCESO HIJO confinado (core/sandbox/runner.py).

El host SOLO supervisa. El hijo aplica el cap de memoria a sí mismo
(child-scoped), muere por SIGKILL al timeout (sin threads zombis en nadie) y
un escape de RestrictedPython compromete únicamente al hijo efímero — que
además corre con env allowlist (build_child_env, sin secretos) y JAMÁS
importa core.* (no carga settings/.env).

Contrato preservado byte-a-byte para los consumidores:
``execute(code, timeout) -> {"success", "text", "image_path"}``
(mismo gate ``allow_code_execution``, mismos textos de timeout/sintaxis).
"""

import asyncio
import json
import logging
import sys
import threading
from pathlib import Path

from core.path_resolver import paths
from core.utils import build_child_env

logger = logging.getLogger(__name__)

_RUNNER = Path(__file__).resolve().parent / "runner.py"
OUTPUT_DIR = paths.charts_dir()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# límite de hijos simultáneos (cada uno importa numpy+matplotlib:
# ~200-300MB RSS). La backpressure de la era in-process ("thread zombi
# envenenado") ya no existe — el timeout MATA al hijo; solo hay rechazo
# si el número de ejecuciones EN CURSO alcanza este tope.
MAX_CONCURRENT_CHILDREN = 4
_CHILD_LOCK = threading.Lock()
_LIVE_CHILDREN = 0


def _cap_stderr(text: str, limit: int = 2000) -> str:
    text = text.strip()
    return text[-limit:] if len(text) > limit else text


class RestrictedExecutor:
    @staticmethod
    async def execute(code: str, timeout: int = 10) -> dict:
        """Ejecuta ``code`` en el hijo confinado. Contrato idéntico al
        executor in-process que reemplaza."""
        from core.config import settings

        if not settings.allow_code_execution:
            return {
                "success": False,
                "error": "code_execution_disabled",
                "output": "Code execution disabled by system configuration.",
            }

        global _LIVE_CHILDREN
        with _CHILD_LOCK:
            if _LIVE_CHILDREN >= MAX_CONCURRENT_CHILDREN:
                return {
                    "success": False,
                    "text": (
                        "❌ Sandbox ocupado: hay demasiadas ejecuciones en curso "
                        f"(máximo {MAX_CONCURRENT_CHILDREN}). Reintenta en unos segundos."
                    ),
                }
            _LIVE_CHILDREN += 1
        try:
            return await RestrictedExecutor._run_child(code, timeout)
        finally:
            with _CHILD_LOCK:
                _LIVE_CHILDREN -= 1

    @staticmethod
    async def _run_child(code: str, timeout: int) -> dict:
        from core.config import settings

        mem_mb = int(getattr(settings, "sandbox_memory_mb", 0) or 0)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(_RUNNER),
                str(OUTPUT_DIR),
                str(mem_mb),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=build_child_env(),
                cwd=str(OUTPUT_DIR),
                start_new_session=True,
            )
        except Exception as e:
            logger.error("No se pudo lanzar el sandbox hijo: %s", e)
            return {
                "success": False,
                "text": f"❌ Execution error: SandboxSpawn\n{e}",
            }

        from tools._subprocess import communicate_or_kill

        timed_out = False
        try:
            stdout, stderr = await communicate_or_kill(
                proc, float(timeout), input=code.encode("utf-8")
            )
        except TimeoutError:
            timed_out = True

        if timed_out:
            # Semántica de la métrica heredada: ejecución que excedió el
            # timeout y fue force-killed (antes: thread zombi envenenado).
            try:
                from core.metrics import metrics as _metrics

                _metrics.record_sandbox_zombie()
            except Exception:
                pass
            logger.warning("Code execution timeout (%s seconds) — hijo SIGKILL", timeout)
            return {
                "text": f"❌ Execution time exceeded (max {timeout} seconds). Possible infinite loop.",
                "success": False,
            }

        if proc.returncode != 0:
            err = _cap_stderr(stderr.decode("utf-8", "replace"))
            logger.error("Sandbox hijo murió rc=%s: %s", proc.returncode, err)
            return {
                "success": False,
                "text": f"❌ Execution error: SandboxCrash (rc={proc.returncode})\n{err}",
            }

        line = stdout.decode("utf-8", "replace").strip().splitlines()
        if not line:
            err = _cap_stderr(stderr.decode("utf-8", "replace"))
            logger.error("Sandbox hijo sin envelope: %s", err)
            return {
                "success": False,
                "text": f"❌ Execution error: SandboxNoEnvelope\n{err}",
            }
        try:
            envelope = json.loads(line[-1])
            return {
                "success": bool(envelope.get("success")),
                "text": str(envelope.get("text", "")),
                "image_path": envelope.get("image_path"),
            }
        except Exception as e:
            logger.error("Envelope del sandbox ilegible: %s", e)
            return {
                "success": False,
                "text": f"❌ Execution error: SandboxBadEnvelope\n{e}",
            }


# Instancia global (contrato previo)
restricted_executor = RestrictedExecutor()
