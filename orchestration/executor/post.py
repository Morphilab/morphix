"""Post-Execution — automatic commit, file verification, and test retry."""

import logging

from core.git_operations import auto_commit
from core.utils import clean_llm_response
from llm import parse_plan_json
from tools.wrapper import safe_tool_call

logger = logging.getLogger(__name__)


from typing import Any


async def _post_execution_checks(
    execution_report: list,
    files_written: list[str],
    commit_done: bool,
    intended_files: set,
    task: str,
    project_root: str | None,
    is_dev_task: bool,
    workspace: str,
    best_agent: str,
    allowed_tools: list[str],
    add_system_message: Any,
) -> str:
    from tools.specs import build_tool_instructions

    report_text = "\n".join(execution_report) if execution_report else ""
    if report_text.strip():
        final_answer = "**Plan ejecutado.**\n\n" + report_text
    else:
        final_answer = ""

    if is_dev_task and files_written and not commit_done:
        await add_system_message("🔄 Realizando commit automático...")
        commit_result = await auto_commit(workspace=workspace, project_root=project_root)
        if commit_result["success"]:
            final_answer += "\n[Commit automático realizado.]"
        else:
            final_answer += "\n⚠️ No se pudo hacer commit automático."

    if intended_files:
        from orchestration.executor.verify import _verify_intended_files

        missing = await _verify_intended_files(intended_files, project_root, workspace)  # type: ignore[arg-type]
        if missing:
            final_answer += f"\n\n⚠️ Faltan archivos: {', '.join(missing)}"

    if "test" in task.lower():
        from orchestration.executor.verify import _check_test_file_exists

        test_found = await _check_test_file_exists(project_root, workspace)  # type: ignore[arg-type]
        if not test_found:
            final_answer += "\n\n⚠️ La tarea pide un test con pytest, pero no se encontró ningún archivo de test."
            await add_system_message("🔧 Reintentando: solicitando creación del test faltante.")
            retry_task = (
                "Crea el archivo de test unitario con pytest que falta. Usa file_manager.write."
            )
            retry_instructions = build_tool_instructions(
                allowed_tools, project_root=project_root, plan_mode=True
            )
            from agents.base import _execute_specialized_agent

            retry_response = await _execute_specialized_agent(
                agent_type=best_agent,
                task=retry_task,
                history=[{"role": "user", "content": retry_task + "\n\n" + retry_instructions}],
                extra_tool_instructions=None,  # type: ignore[arg-type]
            )
            retry_plan = parse_plan_json(clean_llm_response(retry_response))
            if retry_plan and isinstance(retry_plan.get("actions"), list):
                for action in retry_plan["actions"]:
                    if action.get("tool") == "file_manager" and action.get("action") == "write":
                        await add_system_message("🔧 Reintentando acción: file_manager.write")
                        params = dict(action.get("params", {}))
                        if "file_path" in params and "path" not in params:
                            params["path"] = params.pop("file_path")
                        if project_root:
                            params["project_root"] = project_root
                        await safe_tool_call(
                            tool_name="file_manager",
                            parameters={**params, "workspace": workspace, "action": "write"},
                            role="agent",
                        )
                        test_found = await _check_test_file_exists(project_root, workspace)  # type: ignore[arg-type]
                        if test_found:
                            final_answer += "\n✅ Test creado correctamente tras reintento."
                            break
                if not test_found:
                    final_answer += "\n⚠️ No se pudo crear el test automáticamente."

    return final_answer
