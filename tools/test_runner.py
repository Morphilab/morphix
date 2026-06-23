"""Test Runner — ejecuta tests con pytest en el sandbox (Fase 4)."""

import asyncio
import logging
import re
import sys

from agents.audit import log_operation
from core.path_resolver import paths

logger = logging.getLogger(__name__)


def _parse_pytest_counts(output: str) -> dict:
    """Extrae conteos de tests de la línea de resumen de pytest.
    Ej: '3 passed, 1 failed, 2 errors in 0.5s'"""
    patterns = {
        "passed_count": r"(\d+)\s+passed",
        "failed_count": r"(\d+)\s+failed",
        "error_count": r"(\d+)\s+error",
    }
    counts = {"passed_count": 0, "failed_count": 0, "error_count": 0}
    for key, pat in patterns.items():
        match = re.search(pat, output, re.IGNORECASE)
        if match:
            counts[key] = int(match.group(1))
    counts["total_count"] = counts["passed_count"] + counts["failed_count"] + counts["error_count"]
    return counts


async def _test_runner_tool(
    file_path: str,
    workspace: str = "main",
    project_root: str | None = None,
    test_name: str | None = None,
    timeout: int = 30,
) -> dict:
    """Ejecuta tests con pytest. Tool auto-registrada como 'test_runner'.

    Args:
        file_path: Ruta del archivo de test (relativa al proyecto).
        workspace: Workspace activo.
        project_root: Directorio del proyecto.
        test_name: Nombre específico de test a ejecutar (opcional, formato: TestClass::test_method).
        timeout: Timeout máximo en segundos.
    """
    base = paths.memory_dir(workspace)
    if project_root:
        base = base / project_root

    test_file = (base / file_path).resolve()
    base_resolved = base.resolve()

    # Path traversal protection
    try:
        test_file.relative_to(base_resolved)
    except ValueError:
        return {
            "success": False,
            "output": f"❌ Acceso denegado: {file_path} está fuera del workspace.",
        }

    if not test_file.exists():
        return {
            "success": False,
            "output": f"❌ Archivo de test no encontrado: {test_file}",
        }

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        "-v",
        "--tb=short",
        "--override-ini=addopts=",
        f"--rootdir={base}",
        "-p",
        "no:cacheprovider",
    ]
    if test_name:
        cmd.append("-k")
        cmd.append(test_name)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(base),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode()
        counts = _parse_pytest_counts(output)
        tests_were_run = counts["total_count"] > 0
        tests_all_pass = counts["failed_count"] == 0 and counts["error_count"] == 0

        if tests_were_run and tests_all_pass:
            success = True
        else:
            success = proc.returncode == 0

        log_operation("test_runner", str(file_path)[:200], success=success)
        return {
            "success": success,
            "output": output[-4000:],
            "returncode": proc.returncode,
            **counts,
        }
    except TimeoutError:
        return {
            "success": False,
            "output": f"⏱️ Timeout: la ejecución de tests excedió {timeout}s.",
        }
    except Exception as e:
        return {
            "success": False,
            "output": f"❌ Error ejecutando tests: {e}",
        }


# Registro directo en tools_registry
from tools.registry import tools_registry

tools_registry.register("test_runner")(_test_runner_tool)
