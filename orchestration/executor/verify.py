"""Verification — post-execution functional verification and file checking."""

import logging

from core.config import settings
from core.git_operations import auto_commit
from core.path_resolver import paths
from core.utils import clean_llm_response
from llm import parse_plan_json
from orchestration.executor.plan import _execute_plan_actions

logger = logging.getLogger(__name__)


def _extract_and_validate_actions(fix_plan: dict, allowed_tools: list[str]) -> list[dict]:
    if not isinstance(fix_plan, dict):
        return []
    actions = fix_plan.get("actions", [])
    if not isinstance(actions, list):
        return []
    if allowed_tools is None:
        return []
    from tools.specs import expand_allowed_tools

    expanded = expand_allowed_tools(allowed_tools) or []
    return [a for a in actions if isinstance(a, dict) and "tool" in a and a["tool"] in expanded]


from typing import Any


async def _run_functional_verification(
    task: str,
    best_agent: str,
    allowed_tools: list[str],
    project_root: str | None,
    intended_files: set,
    add_system_message: Any,
    workspace: str | None = None,
    events: Any = None,
) -> bool:
    if workspace is None:
        workspace = settings.active_workspace
    from llm.prompts import PLAN_VERIFY_PROMPT

    file_contents = {}
    base = paths.memory_dir(workspace)
    if project_root:
        base = base / project_root

    for fname in intended_files:
        fpath = base / fname
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            file_contents[fname] = content

    if not file_contents:
        return None  # type: ignore[return-value]

    files_text = "\n\n".join(f"--- {name} ---\n{cont}" for name, cont in file_contents.items())
    prompt = PLAN_VERIFY_PROMPT.format(task=task, files_content=files_text)

    from agents.base import _execute_specialized_agent

    raw_response = await _execute_specialized_agent(
        agent_type=best_agent, task=prompt, history=[], extra_tool_instructions=None  # type: ignore[arg-type]
    )
    response_text = clean_llm_response(raw_response)
    result = parse_plan_json(response_text)

    if result and not result.get("is_correct", True):
        fix_plan = result.get("fix_plan", {})
        fix_actions = _extract_and_validate_actions(fix_plan, allowed_tools)
        if not fix_actions:
            await add_system_message(
                "⚠️ El plan de corrección no tiene acciones válidas. Reintentando..."
            )
            retry_prompt = (
                prompt + "\n\n⚠️ Tu respuesta anterior no contenía un array 'actions' válido. "
                "Solo puedes usar 'file_manager' y 'git_manager'."
            )
            raw_response = await _execute_specialized_agent(
                agent_type=best_agent, task=retry_prompt, history=[], extra_tool_instructions=None  # type: ignore[arg-type]
            )
            response_text = clean_llm_response(raw_response)
            result = parse_plan_json(response_text)

    if not result:
        return None  # type: ignore[return-value]

    if result.get("is_correct"):
        return "✅ Verificación funcional superada: el contenido cumple con los requisitos."  # type: ignore[return-value]

    fix_plan = result.get("fix_plan", {})
    fix_actions = _extract_and_validate_actions(fix_plan, allowed_tools)

    if fix_actions:
        await add_system_message("🔧 Aplicando correcciones detectadas por la verificación...")
        report, written, commit_done, _ = await _execute_plan_actions(
            fix_actions, project_root, workspace, add_system_message
        )
        if written and not commit_done:
            await auto_commit(
                workspace=workspace,
                project_root=project_root,
                message="Correcciones automáticas",
            )
        result_msg: str = (  # type: ignore[return-value]
            "🔧 Verificación Funcional: se detectaron incumplimientos y se aplicaron correcciones.\n"
            + "\n".join(report)
        )
        return result_msg  # type: ignore[return-value]

    return "⚠️ La verificación encontró incumplimientos, pero el plan de corrección no es válido."  # type: ignore[return-value]


async def _verify_intended_files(
    file_paths: set, project_root: str, workspace: str | None = None
) -> list:
    if workspace is None:
        workspace = settings.active_workspace
    missing = []
    for path in file_paths:
        if project_root:
            relative = paths.normalize_path(path, project_root)
            full_path = paths.memory_dir(workspace) / project_root / relative
        else:
            full_path = paths.memory_dir(workspace) / path
        if not full_path.exists():
            missing.append(path)
    return missing


async def _check_test_file_exists(project_root: str, workspace: str | None = None) -> bool:
    if workspace is None:
        workspace = settings.active_workspace
    try:
        base = paths.memory_dir(workspace)
        search_dir = base / project_root if project_root else base
        if not search_dir.exists():
            return False
        for py_file in search_dir.rglob("*.py"):
            if "test" in py_file.name.lower():
                return True
        return False
    except Exception:
        return False
