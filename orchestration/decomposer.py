# orchestration/decomposer.py
"""
Task Decomposer: divide una tarea en subtareas para el orquestador.
"""

import logging
import re

from core.config import settings
from core.path_resolver import paths
from core.utils import clean_llm_response, truncar_en_palabra
from llm import models, parse_json_from_llm

logger = logging.getLogger(__name__)


def _recent_history_context(conversation_history: list | None) -> str:
    """Últimos 6 mensajes user/assistant como contexto de continuación.

    Compartido por decompose_task y decompose_task_dag.
    """
    if not conversation_history:
        return ""
    last_msgs = conversation_history[-6:]
    return "\n".join(
        f"[{m['role']}]: {str(m.get('content', ''))[:200]}"
        for m in last_msgs
        if m.get("role") in ("user", "assistant")
    )


async def _append_rate_limit_hint(prompt: str, threshold: int, hint: str, log_msg: str) -> str:
    """Añade una nota de rate-limit al prompt si el rate restante está bajo.

    Compartido por decompose_task y decompose_task_dag.
    """
    try:
        from core.rate_limiter import get_rate_limiter

        rl = get_rate_limiter()
        remaining = await rl.remaining()
        if remaining < threshold:
            prompt += hint
    except Exception:
        logger.warning(log_msg, exc_info=True)
    return prompt


async def _build_project_context(project_root: str | None, workspace: str | None = None) -> str:
    """Scan the project to provide real context to the decomposer LLM.

    Offload SIEMPRE a thread — future.result() síncrono congela el event
    loop (UI incluida) hasta 10s en proyectos grandes.
    """
    import asyncio

    return await asyncio.to_thread(_scan_project_sync, project_root, workspace)


def _scan_project_sync(project_root: str | None, workspace: str | None = None) -> str:
    """Synchronous project scan (runs in thread when called from async context)."""
    if workspace is None:
        workspace = settings.active_workspace
    if not project_root:
        return "PROYECTO NUEVO — sin archivos. La primera subtarea debe CREAR la estructura."

    base = paths.project_dir(workspace, project_root)
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
                logger.warning("Failed to read README for project context", exc_info=True)
            break

    # Find main script
    for pattern in ("main.*", "*.py", "src/*.py", "*.sh", "*.js"):
        for candidate in sorted(base.glob(pattern)):
            if candidate.is_file():
                try:
                    content = candidate.read_text()[:600]
                    parts.append(f"\nArchivo principal ({candidate.name}):\n{content}")
                except Exception:
                    logger.warning(
                        "Failed to read main project file for decomposer context", exc_info=True
                    )
                break
        else:
            continue
        break

    return "\n\n".join(parts)


def _project_has_files(project_root: str | None, workspace: str | None) -> bool:
    """True si el proyecto ya tiene archivos (no es nuevo)."""
    if not project_root:
        return False
    if workspace is None:
        workspace = settings.active_workspace
    base = paths.project_dir(workspace, project_root)
    try:
        return base.exists() and any(base.iterdir())
    except OSError:
        return False


async def decompose_task(
    query: str,
    is_follow_up: bool = False,
    conversation_history: list[dict] | None = None,
    project_root: str | None = None,
    max_subtasks: int | None = None,
    workspace: str | None = None,
) -> list[str]:
    """Descompone la tarea en 2-5 subtareas claras y accionables"""
    if workspace is None:
        workspace = settings.active_workspace
    from llm.prompts import DECOMPOSE_TASK_PROMPT

    project_context = await _build_project_context(project_root, workspace)
    prompt = DECOMPOSE_TASK_PROMPT.format(query=query, project_context=project_context)

    if is_follow_up:
        history_context = _recent_history_context(conversation_history)
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
    prompt = await _append_rate_limit_hint(
        prompt,
        threshold=10,
        hint="\nIMPORTANTE: Genera máximo 2 subtareas, el rate de API está bajo.",
        log_msg="Rate limiter check failed, continuing without throttle hint",
    )

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
        # EXCEPCIÓN — tarea trivial (query corta, proyecto nuevo, 1
        # subtarea del LLM) se respeta sin fantasma "Verificar...".
        if len(final_subtasks) < 2:
            trivial = (
                len(final_subtasks) == 1
                and len(query.strip()) < 120
                and not _project_has_files(project_root, workspace)
            )
            if not trivial:
                logger.warning("Decompose_task generó <2 subtareas → forzando división")
                if len(final_subtasks) == 1:
                    final_subtasks.append(
                        f"Verificar y validar que {truncar_en_palabra(final_subtasks[0], 80)} funciona correctamente"
                    )
                else:
                    final_subtasks = [
                        truncar_en_palabra(query, 100),
                        f"Verificar el resultado de: {truncar_en_palabra(query, 80)}",
                    ]

        # Max limit from feature flags

        if max_subtasks is None:
            max_subtasks = settings.max_subtasks
        final_subtasks = final_subtasks[:max_subtasks]

        logger.info(f"✅ Decompose_task generó {len(final_subtasks)} subtareas")
        return final_subtasks

    except Exception as e:
        logger.error(f"Error grave en decompose_task: {e}", exc_info=True)
        # Safe fallback with safety floor (minimum 2 subtasks)
        return [
            truncar_en_palabra(query, 100),
            f"Verificar el resultado de: {truncar_en_palabra(query, 80)}",
        ]


_DAG_PROMPT = """Descompón la tarea en subtareas CON DEPENDENCIAS explícitas.

Tarea:
{query}

Contexto del proyecto:
{project_context}

Responde ÚNICAMENTE con un JSON válido con esta forma exacta:
{{
  "tasks": [
    {{"id": "t1", "title": "primera subtarea", "depends_on": []}},
    {{"id": "t2", "title": "segunda subtarea", "depends_on": ["t1"]}}
  ]
}}

Reglas:
- ids cortos (t1, t2, ...), únicos, referenciados solo en depends_on de otras tasks.
- depends_on lista los ids que DEBEN terminar antes de que esta task empiece.
- 2 a 6 tasks. Sin dependencias circulares. Un task sin dependencias va en depends_on: [].
- Sin texto fuera del JSON."""


def _validar_dag(tasks: list[dict]) -> list[list[dict]]:
    """Valida el DAG (ids únicos, deps existentes, sin ciclos) y retorna
    los niveles topológicos (Kahn). Lanza ValueError con mensaje accionable
    ante cualquier violación — el motor no adivina dependencias rotas."""
    if not isinstance(tasks, list) or len(tasks) < 1:
        raise ValueError("dag sin tasks")
    ids = [str(t.get("id") or "").strip() for t in tasks]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("tasks con ids vacíos o duplicados")
    id_set = set(ids)
    for t in tasks:
        for dep in t.get("depends_on") or []:
            if dep not in id_set:
                raise ValueError(f"task '{t['id']}' depende de id inexistente '{dep}'")
    # Kahn: niveles de ejecución
    pendientes = {str(t["id"]): t for t in tasks}
    hechos: set[str] = set()
    niveles: list[list[dict]] = []
    while pendientes:
        listos = [
            t for t in pendientes.values() if all(d in hechos for d in (t.get("depends_on") or []))
        ]
        if not listos:
            raise ValueError("dependencias circulares en el dag")
        niveles.append(list(listos))
        for t in listos:
            hechos.add(str(t["id"]))
            pendientes.pop(str(t["id"]))
    return niveles


async def decompose_task_dag(query: str, workspace: str | None = None) -> dict:
    """Descomposición con dependencias (strategy: dag del DSL).

    Retorna {"tasks": [...], "levels": [[task, ...], ...]} — levels es la
    ordenación topológica lista para consumirse nivel a nivel. Falla loud
    (ValueError) ante JSON inválido, ids rotos o ciclos: el motor no
    adivina dependencias.
    """
    if workspace is None:
        workspace = settings.active_workspace
    project_context = await _build_project_context(None, workspace)
    prompt = _DAG_PROMPT.format(query=query, project_context=project_context)

    response = await models.call(
        messages=[{"role": "user", "content": prompt}],
        role="reasoning",
        temperature=0.1,
    )
    raw = clean_llm_response(response)
    data = parse_json_from_llm(raw)
    tasks = (data or {}).get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        raise ValueError("el modelo no devolvió el JSON de tasks del dag")
    levels = _validar_dag(tasks)
    logger.info("decompose dag: %d tasks en %d niveles", len(tasks), len(levels))
    return {"tasks": tasks, "levels": levels}
