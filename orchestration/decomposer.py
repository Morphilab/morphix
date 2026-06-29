# features/maestro/services/task_decomposer.py
"""
Task Decomposer - Versión FINAL robusta y refinada (Fase 6)
"""

import logging
import re

from core.path_resolver import paths
from core.utils import clean_llm_response
from llm import models, parse_json_from_llm

logger = logging.getLogger(__name__)


def _build_project_context(project_root: str | None) -> str:
    """Scan the project to provide real context to the decomposer LLM."""
    if not project_root:
        return "PROYECTO NUEVO — sin archivos. La primera subtarea debe CREAR la estructura."

    base = paths.memory_dir("main") / project_root
    if not base.exists():
        return "PROYECTO NUEVO — sin archivos. La primera subtarea debe CREAR la estructura."

    files = list(base.rglob("*"))
    if not files:
        return "PROYECTO NUEVO — sin archivos. La primera subtarea debe CREAR la estructura."

    parts = [
        "PROYECTO EXISTENTE detectado. La primera subtarea debe LEER y ANALIZAR "
        "los archivos existentes antes de crear o modificar nada."
    ]

    # Read README if exists
    for readme_name in ("README.md", "README.txt", "README"):
        readme = base / readme_name
        if readme.exists():
            try:
                content = readme.read_text()[:600]
                parts.append(f"README.md preview:\n{content}")
            except Exception:
                pass
            break

    # Find main script
    for pattern in ("*.sh", "*.py", "main.*", "src/*.py", "*.js"):
        for candidate in sorted(base.glob(pattern)):
            if candidate.is_file():
                try:
                    content = candidate.read_text()[:600]
                    parts.append(f"\nArchivo principal ({candidate.name}):\n{content}")
                except Exception:
                    pass
                break
        else:
            continue
        break

    return "\n\n".join(parts)


async def decompose_task(
    query: str,
    is_follow_up: bool = False,
    conversation_history: list[dict] | None = None,
    project_root: str | None = None,
) -> list[str]:
    """Descompone la tarea en 2-5 subtareas claras y accionables"""
    from llm.prompts import DECOMPOSE_TASK_PROMPT

    project_context = _build_project_context(project_root)
    prompt = DECOMPOSE_TASK_PROMPT.format(query=query, project_context=project_context)

    if is_follow_up:
        history_context = ""
        if conversation_history:
            last_msgs = conversation_history[-6:]
            history_context = "\n".join(
                f"[{m['role']}]: {str(m.get('content', ''))[:200]}"
                for m in last_msgs
                if m.get("role") in ("user", "assistant")
            )
        prompt = (
            "⚠️ CONTEXTO IMPORTANTE: Esta es una conversación DE CONTINUACIÓN. "
            "El proyecto YA EXISTE en disco con archivos creados previamente. "
            "NO crees subtareas para crear archivos que ya existen. "
            "Enfócate en MODIFICAR, EXTENDER o CORREGIR lo existente. "
            "Subtareas sugeridas: leer archivos existentes, hacer cambios puntuales, "
            "ejecutar tests, verificar.\n\n"
            f"Historial reciente de la conversación:\n{history_context}\n\n" + prompt
        )

    # Rate limiter awareness: request fewer subtasks if rate is low
    try:
        from core.rate_limiter import get_rate_limiter

        rl = get_rate_limiter()
        remaining = await rl.remaining()
        if remaining < 10:
            prompt += "\nIMPORTANTE: Genera máximo 2 subtareas, el rate de API está bajo."
    except Exception:
        pass

    try:
        response = await models.call(
            messages=[{"role": "user", "content": prompt}],
            role="reasoning",
            temperature=0.1,
        )

        raw = clean_llm_response(response)
        logger.debug(f"Respuesta cruda de decompose_task: {raw[:700]}...")

        data = parse_json_from_llm(raw)

        # Extraer subtareas
        subtasks = []
        if data and isinstance(data.get("subtasks"), list):
            subtasks = data["subtasks"]
        else:
            # Fallback regex fuerte
            subtasks = (
                re.findall(r"[-•]\s*(.+?)(?=\n|$)", raw)
                or re.findall(r"\d+\.\s*(.+?)(?=\n|$)", raw)
                or re.findall(r'["\'](.+?)["\']', raw)
            )

        # Limpieza y filtrado final
        final_subtasks = []
        for s in subtasks:
            if isinstance(s, dict):
                clean = str(s.get("description", s.get("task", str(s)))).strip()
            else:
                clean = str(s).strip()
            if len(clean) > 8 and clean.lower() not in {"subtasks", "subtask", ""}:
                final_subtasks.append(clean)

        # Safety: always return at least 2 subtasks for developer tasks
        if len(final_subtasks) < 2:
            logger.warning("Decompose_task generó <2 subtareas → forzando división")
            if len(final_subtasks) == 1:
                final_subtasks.append(
                    f"Verificar y validar que {final_subtasks[0][:80]} funciona correctamente"
                )
            else:
                final_subtasks = [query[:100], f"Verificar el resultado de: {query[:80]}"]

        # Max limit from feature flags
        from core.config import settings

        max_subtasks = settings.max_subtasks
        final_subtasks = final_subtasks[:max_subtasks]

        logger.info(f"✅ Decompose_task generó {len(final_subtasks)} subtareas")
        return final_subtasks

    except Exception as e:
        logger.error(f"Error grave en decompose_task: {e}", exc_info=True)
        # Safe fallback with safety floor (minimum 2 subtasks)
        return [query[:100], f"Verificar el resultado de: {query[:80]}"]


async def decompose_task_with_phases(
    query: str,
    is_follow_up: bool = False,
    conversation_history: list[dict] | None = None,
    project_root: str | None = None,
) -> dict:
    """Descompone la tarea en fases con subtareas, para blackboard multi-phase.

    Returns:
        {"phases": [...], "strategy": "sequential"}
        or fallback: {"phases": [{"subtasks": [...]}]} (single phase)
    """
    from llm.prompts import DECOMPOSE_TASK_WITH_PHASES_PROMPT

    project_context = _build_project_context(project_root)
    prompt = DECOMPOSE_TASK_WITH_PHASES_PROMPT.format(query=query, project_context=project_context)

    if is_follow_up:
        history_context = ""
        if conversation_history:
            last_msgs = conversation_history[-6:]
            history_context = "\n".join(
                f"[{m['role']}]: {str(m.get('content', ''))[:200]}"
                for m in last_msgs
                if m.get("role") in ("user", "assistant")
            )
        prompt = (
            "⚠️ CONTEXTO IMPORTANTE: Esta es una conversación DE CONTINUACIÓN. "
            "El proyecto YA EXISTE en disco. Usa máximo 2 fases. "
            "Enfócate en MODIFICAR lo existente.\n\n"
            f"Historial reciente:\n{history_context}\n\n" + prompt
        )

    # Rate limiter awareness
    try:
        from core.rate_limiter import get_rate_limiter

        rl = get_rate_limiter()
        remaining = await rl.remaining()
        if remaining < 5:
            prompt += (
                "\nIMPORTANTE: Genera máximo 2 fases con 2 subtareas cada una (API rate bajo)."
            )
    except Exception:
        pass

    try:
        response = await models.call(
            messages=[{"role": "user", "content": prompt}],
            role="reasoning",
            temperature=0.1,
        )

        raw = clean_llm_response(response)
        data = parse_json_from_llm(raw)

        if data and isinstance(data.get("phases"), list):
            phases = data["phases"]
            # Validate and clean phases
            valid_phases: list[dict] = []
            for p in phases:
                if isinstance(p, dict) and p.get("subtasks"):
                    subtasks = [
                        str(s).strip() if isinstance(s, str) else str(s.get("description", s))
                        for s in p["subtasks"]
                    ]
                    valid_phases.append(
                        {
                            "phase": str(p.get("phase", "default")),
                            "order": int(p.get("order", len(valid_phases) + 1)),
                            "description": str(p.get("description", "")),
                            "subtasks": subtasks,
                        }
                    )
            if valid_phases:
                logger.info(f"✅ decompose_task_with_phases: {len(valid_phases)} fases")
                return {"phases": valid_phases, "strategy": data.get("strategy", "sequential")}

    except Exception as e:
        logger.warning(f"decompose_task_with_phases falló, usando single-phase: {e}")

    # Fallback: single phase with decompose_task
    subtasks = await decompose_task(query, is_follow_up, conversation_history, project_root)
    return {
        "phases": [
            {
                "phase": "default",
                "order": 1,
                "description": "Implementación principal",
                "subtasks": subtasks,
            }
        ],
        "strategy": "sequential",
    }
