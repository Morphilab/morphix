"""TDD Loop — ciclo de Test-Driven Development automatizado.

Complete TDD loop:
  - Ejecuta tests existentes
  - Si fallan, invoca al agente para corregir el código
  - Repite hasta que todos los tests pasen o se alcance el límite
"""

import asyncio
import logging

from core.config import settings
from core.path_resolver import paths
from tools.wrapper import safe_tool_call

logger = logging.getLogger(__name__)

MAX_TDD_ITERATIONS = 5

_TEST_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".undo", ".redo"}


def _project_has_no_tests(workspace: str, project_root: str | None, files_modified: list) -> bool:
    """True if the project has no test files yet (green-field).

    Escanea el directorio del proyecto en busca de 'test_*.py' / '*_test.py'.
    Señal fiable, independiente del output truncado de pytest.
    """
    if not project_root:
        return not files_modified
    proj_dir = paths.code_projects_dir(workspace, project_root)
    if not proj_dir.exists():
        return not files_modified
    for pattern in ("test_*.py", "*_test.py"):
        for p in proj_dir.rglob(pattern):
            if not any(part in _TEST_SKIP_DIRS for part in p.parts):
                return False
    return True


async def execute_tdd_loop(
    task: str,
    workspace: str | None = None,
    project_root: str | None = None,
    allowed_tools: list | None = None,
    agent_type: str | None = None,
    conversation_history: list | None = None,
    max_iterations: int = MAX_TDD_ITERATIONS,
) -> dict:
    """Ciclo TDD automatizado completo.

    Flujo real:
    1. Ejecutar tests existentes → ver fallos
    2. Si hay fallos, invocar al agente con el contexto de fallos
    3. El agente corrige el código vía file_manager / diff_editor
    4. Volver a ejecutar tests
    5. Repetir hasta que todos pasen o se alcance max_iterations

    Args:
        task: Descripción de la tarea a implementar.
        workspace: Workspace activo.
        project_root: Directorio del proyecto.
        allowed_tools: Herramientas permitidas para el agente.
        agent_type: Tipo de agente a usar para las correcciones.
        conversation_history: Historial de conversación previo.
        max_iterations: Máximo de iteraciones del ciclo.

    Returns:
        {"status": "completed"|"failed", "result": str, "iterations": int}
    """
    if workspace is None:
        workspace = settings.active_workspace
    from orchestration.loop import execute_agent_loop

    tdd_tools = allowed_tools or ["file_manager", "diff_editor", "test_runner", "git_manager"]

    iterations = 0
    test_output = ""
    files_modified: list[str] = []

    while iterations < max_iterations:
        iterations += 1
        logger.info("🔄 TDD Loop iteración %d/%d", iterations, max_iterations)

        # 1. Ejecutar tests
        try:
            test_result = await safe_tool_call(
                tool_name="test_runner",
                parameters={
                    "file_path": ".",
                    "workspace": workspace,
                    "project_root": project_root,
                },
                role="agent",
            )
        except Exception as e:
            logger.error(f"TDD test_runner call failed: {e}")
            test_result = {
                "success": False,
                "error": str(e),
                "output": {
                    "output": f"Error running tests: {e}",
                    "success": False,
                    "failed_count": 1,
                    "error_count": 1,
                },
            }

        test_output_raw = test_result if isinstance(test_result, dict) else {}
        inner_output = (
            test_output_raw.get("output", {})
            if isinstance(test_output_raw.get("output"), dict)
            else {}
        )
        test_output = str(inner_output.get("output", str(test_result)))
        test_success = inner_output.get("success", False)
        failed_count = inner_output.get("failed_count", 0)
        error_count = inner_output.get("error_count", 0)

        if test_success and failed_count == 0 and error_count == 0:
            logger.info("✅ TDD Loop: todos los tests pasan en iteración %d", iterations)
            return {
                "status": "completed",
                "result": f"✅ Todos los tests pasan en iteración {iterations}.\n\n{test_output[:2000]}",
                "iterations": iterations,
                "files_modified": files_modified,
            }

        # 2. Invoke the agent: write tests (green-field) or fix failures
        no_tests = _project_has_no_tests(workspace, project_root, files_modified)
        if no_tests:
            impl_context = (
                f"Tarea: {task}\n\n"
                "Aún NO existen tests en el proyecto. Implementa con TDD:\n"
                "1. PRIMERO escribe los tests con pytest usando file_manager (action=write); "
                "nómbralos 'test_*.py' (p.ej. 'test_es_primo.py').\n"
                "2. LUEGO escribe la implementación para que los tests pasen.\n"
                "Usa SIEMPRE rutas RELATIVAS al proyecto (ej: 'es_primo.py'); "
                "NUNCA rutas absolutas ni '/'."
            )
        else:
            impl_context = (
                f"Tarea original: {task}\n\n"
                f"Resultados de tests (iteración {iterations}):\n{test_output[:3000]}\n\n"
                f"Fallos: {failed_count}, Errores: {error_count}.\n\n"
                "Analiza los fallos y CORRIGE el código para que los tests pasen. "
                "Usa file_manager (action=read) para leer archivos existentes, "
                "y file_manager (action=write) o diff_editor (action=apply) para modificarlos. "
                "Después de cada cambio, explica qué corregiste y por qué."
            )

        logger.info(
            "TDD Loop: %s (fallos=%d, errores=%d)",
            "sin tests aún → escribiendo tests + implementación" if no_tests else "corrigiendo",
            failed_count,
            error_count,
        )

        try:
            agent_result = await asyncio.wait_for(
                execute_agent_loop(
                    task=impl_context,
                    agent_type=agent_type,
                    history=conversation_history,
                    allowed_tools=tdd_tools,
                    project_root=project_root,
                    workspace=workspace,
                    extra_context=f"Modo TDD. Iteración {iterations}/{max_iterations}.",
                ),
                timeout=300,
            )
        except TimeoutError:
            logger.warning("TDD Loop: iteración %d agotó timeout (300s)", iterations)
            return {
                "status": "failed",
                "result": f"TDD iteration {iterations} timed out after 300s.",
                "iterations": iterations,
                "files_modified": files_modified,
            }

        agent_files = agent_result.get("files_written", [])
        for f in agent_files:
            if f not in files_modified:
                files_modified.append(f)

        if agent_result.get("status") == "stalled":
            logger.warning("TDD Loop: agente estancado en iteración %d", iterations)
            return {
                "status": "failed",
                "result": (
                    f"⚠️ Agente estancado en iteración {iterations}.\n\n"
                    f"Último resultado de tests:\n{test_output[:2000]}\n\n"
                    f"{agent_result['result']}"
                ),
                "iterations": iterations,
                "files_modified": files_modified,
            }

    return {
        "status": "failed",
        "result": (
            f"⚠️ Límite de {max_iterations} iteraciones alcanzado.\n\n"
            f"Últimos resultados:\n{test_output[:2000]}"
        ),
        "iterations": iterations,
        "files_modified": files_modified,
    }
