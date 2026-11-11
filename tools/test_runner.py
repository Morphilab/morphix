"""Test Runner — ejecuta tests con pytest en el sandbox."""

import asyncio
import importlib.util
import logging
import re
import sys
from pathlib import Path

from agents.audit import log_operation
from core.config import settings
from core.path_resolver import paths

logger = logging.getLogger(__name__)

_META_TEST_RE = re.compile(
    r"(pytest\.main\s*\()|((subprocess|os\.system|os\.popen).{0,400}(-m\s+['\"]?pytest|['\"]pytest['\"]))",
    re.IGNORECASE | re.DOTALL,
)


def _meta_test_reason(rutas: list) -> str | None:
    """Detecta tests que ejecutan la suite dentro de la suite.

    Un test que corre pytest desde un test es una recursión: el pytest
    anidado vuelve a ejecutar el mismo test. El motor no puede adivinar la
    intención; el patrón es determinista y el mensaje es accionable.
    """
    # Un `file_path: tests` (gate de la suite completa) es un DIRECTORIO: hay
    # que inspeccionar sus tests, o el guard no ve la recursión que previene.
    candidatas: list = []
    for ruta in rutas:
        if ruta.is_dir():
            candidatas.extend(sorted(ruta.rglob("test_*.py"))[:200])
        else:
            candidatas.append(ruta)
    for ruta in candidatas:
        try:
            texto = ruta.read_text(encoding="utf-8", errors="ignore")[:200_000]
        except OSError:
            continue
        if _META_TEST_RE.search(texto):
            return (
                f"❌ test_runner: {ruta.name} ejecuta pytest dentro de un test "
                "(pytest.main/subprocess). Escribí un test de COMPORTAMIENTO; "
                "no ejecutes la suite desde el test (recursión)."
            )
    return None


def _parse_pytest_counts(output: str) -> dict:
    """Extrae conteos de tests de la línea de resumen de pytest.
    Ej: '3 passed, 1 failed, 2 errors in 0.5s'

    Sin resumen parseable NO hay medición: devuelve {} (claves ausentes).
    Devolver ceros hacía creer a los consumidores que se había medido
    cero tests, y el until agotaba el loop sobre un artefacto inexistente."""
    # El resumen de pytest va AL FINAL: se toma la última coincidencia de cada
    # patrón. Sin el lookbehind, un test llamado `...latin1 PASSED` (modo -v)
    # aportaba "1 passed" y el conteo real de 101 se reportaba como 1.
    patterns = {
        "passed_count": r"(?<![\w])(\d+)\s+passed\b",
        "failed_count": r"(?<![\w])(\d+)\s+failed\b",
        "error_count": r"(?<![\w])(\d+)\s+errors?\b",
    }
    counts: dict[str, int] = {}
    for key, pat in patterns.items():
        matches = re.findall(pat, output, re.IGNORECASE)
        if matches:
            counts[key] = int(matches[-1])
    if not counts:
        return {}
    counts.setdefault("passed_count", 0)
    counts.setdefault("failed_count", 0)
    counts.setdefault("error_count", 0)
    counts["total_count"] = counts["passed_count"] + counts["failed_count"] + counts["error_count"]
    return counts


async def _test_runner_tool(
    file_path: str = "",
    file_paths: list[str] | None = None,
    workspace: str | None = None,
    project_root: str | None = None,
    test_name: str | None = None,
    timeout: int = 30,
) -> dict:
    """Ejecuta tests con pytest. Tool auto-registrada como 'test_runner'.

    Args:
        file_path: Ruta del archivo de test (relativa al proyecto).
        file_paths: Varias rutas para una sola pasada (p.ej. la verificación
            del agregado: solo lo que un run escribió). Vacío/None → file_path.
        workspace: Workspace activo.
        project_root: Directorio del proyecto.
        test_name: Nombre específico de test a ejecutar (opcional, formato: TestClass::test_method).
        timeout: Timeout máximo en segundos.
    """
    if workspace is None:
        workspace = settings.active_workspace
    base = paths.memory_dir(workspace)
    if project_root:
        base = base / project_root

    base_resolved = base.resolve()

    # file_paths (lista o separado por comas) habilita la verificación del
    # agregado: solo los artefactos que un run tocó. Cualquier ruta fuera de
    # la base o inexistente fail-fast — sin conteos, sin medición falsa.
    if isinstance(file_paths, (list, tuple)):
        rutas = [str(r).strip() for r in file_paths if str(r).strip()]
    elif str(file_paths or "").strip():
        rutas = [r.strip() for r in str(file_paths).split(",") if r.strip()]
    else:
        rutas = [str(file_path or "").strip()]
    if not any(rutas):
        return {
            "success": False,
            "output": "❌ test_runner: sin rutas que verificar (file_path/file_paths vacíos).",
        }

    resueltas: list[str] = []
    for ruta in rutas:
        test_file = (base / ruta).resolve()
        try:
            test_file.relative_to(base_resolved)
        except ValueError:
            return {
                "success": False,
                "output": f"❌ Acceso denegado: {ruta} está fuera del workspace.",
            }
        if not test_file.exists():
            return {
                "success": False,
                "output": f"❌ Archivo de test no encontrado: {test_file}",
            }
        resueltas.append(str(test_file))

    motivo = _meta_test_reason([Path(r) for r in resueltas])
    if motivo is not None:
        return {"success": False, "output": motivo}

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *resueltas,
        "-v",
        "--tb=short",
        "--override-ini=addopts=",
        f"--rootdir={base}",
        "-p",
        "no:cacheprovider",
    ]
    # Un test colgado no puede costar el timeout global del step: pytest-timeout
    # lo corta por test y pytest lo reporta como failed (medición roja), no como
    # agregado sin medición. Si el plugin no está instalado, se omite el flag.
    if importlib.util.find_spec("pytest_timeout") is not None:
        # El plugin se auto-carga por entry point: `-p pytest_timeout` lo
        # registraba DOS veces (ValueError "Plugin already registered").
        cmd += [f"--timeout={settings.pytest_timeout_s}"]
    if test_name:
        cmd.append("-k")
        cmd.append(test_name)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(base),
            start_new_session=True,
        )
        from tools._subprocess import communicate_or_kill

        stdout, _ = await communicate_or_kill(proc, timeout=timeout)
        output = stdout.decode()
        counts = _parse_pytest_counts(output)
        tests_were_run = counts.get("total_count", 0) > 0
        tests_all_pass = (
            tests_were_run
            and counts.get("failed_count", 0) == 0
            and counts.get("error_count", 0) == 0
        )

        if tests_were_run and tests_all_pass:
            success = True
        else:
            success = proc.returncode == 0

        log_operation("test_runner", str(file_path)[:200], success=success)
        return {
            "success": success,
            "output": output[-4000:],
            "returncode": proc.returncode,
            **(counts if tests_were_run else {}),
        }
    except TimeoutError:
        return {
            "success": False,
            "output": f"⏱️ Timeout: la ejecución de tests excedió {timeout}s.",
            "error_kind": "timeout",
        }
    except Exception as e:
        return {
            "success": False,
            "output": f"❌ Error ejecutando tests: {e}",
        }


# Registro directo en tools_registry
from tools.registry import tools_registry

tools_registry.register("test_runner")(_test_runner_tool)
