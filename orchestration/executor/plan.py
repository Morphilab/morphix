"""Plan Executor — genera y ejecuta planes de acciones desde el LLM."""

import logging

from core.path_resolver import paths
from tools.wrapper import safe_tool_call

logger = logging.getLogger(__name__)


from typing import Any


async def _resolve_agent_and_task(
    task: str,
    conversation_history: list[dict],
    forced_agent: str | None,
    agents_registry: Any,
) -> tuple[str, str]:
    if forced_agent and forced_agent in agents_registry.list_agents():
        agent = forced_agent
    else:
        from orchestration.router import agent_router

        agent = await agent_router.select_best_agent(task)

    return agent, task


async def _verify_file_written(params: dict, workspace: str, project_root: str) -> dict:
    try:
        from tools.file_manager import FileManager

        path = params.get("path") or params.get("file_path")
        if not path:
            return {"success": False, "message": "No se pudo verificar: falta ruta."}
        await FileManager.execute(
            action="read",
            path=path,
            workspace=workspace,
            project_root=project_root,
        )
        return {"success": True}
    except FileNotFoundError:
        return {
            "success": False,
            "message": f"El archivo '{path}' no se encontró después de escribirlo.",
        }
    except Exception as e:
        return {"success": False, "message": f"Error de verificación: {str(e)[:120]}"}


async def _execute_single_action(
    tool_name: str,
    action: str,
    params: dict,
    workspace: str,
    project_root: str | None,
) -> dict:
    tool_result = await safe_tool_call(
        tool_name=tool_name,
        parameters={**params, "workspace": workspace, "action": action},
        role="agent",
    )
    result_text = tool_result.get("output", str(tool_result))
    wrote = False
    committed = False

    if tool_name == "file_manager" and action == "write":
        if isinstance(result_text, str) and result_text.startswith("❌ Error de sintaxis"):
            report = f"- **{tool_name}.{action}** → {result_text}"
        else:
            wrote = True
            verify_result = await _verify_file_written(params, workspace, project_root)  # type: ignore[arg-type]
            if not verify_result["success"]:
                report = f"- **{tool_name}.{action}** → {result_text}. ⚠️ Verificación: {verify_result['message']}"
            else:
                report = f"- **{tool_name}.{action}** → archivo escrito correctamente."
    elif tool_name == "file_manager" and action == "read":
        if isinstance(result_text, str) and len(result_text) > 200:
            report = f"- **{tool_name}.{action}** → archivo leído ({len(result_text)} caracteres)."
        else:
            report = f"- **{tool_name}.{action}** → {result_text}"
    elif tool_name == "git_manager" and action == "commit":
        if "Commit realizado" in str(result_text):
            committed = True
        report = f"- **{tool_name}.{action}** → {result_text}"
    else:
        report = f"- **{tool_name}.{action}** → {result_text}"

    return report, wrote, committed  # type: ignore[return-value]


async def _execute_plan_actions(
    actions: list[dict],
    project_root: str | None,
    workspace: str,
    add_system_message: Any,
) -> tuple[list, bool, bool, set]:
    base_path = paths.memory_dir(workspace)
    base_path_project = base_path / project_root if project_root else base_path

    def _refresh_existing_files() -> set:
        existing = set()
        if base_path_project.exists():
            for f in base_path_project.rglob("*"):
                if f.is_file():
                    existing.add(str(f.relative_to(base_path_project)))
        return existing

    existing_files = _refresh_existing_files()

    intended_files = set()
    execution_report = []
    files_written = False
    commit_done = False

    for idx, action in enumerate(actions):
        tool_name = action.get("tool", "").lower()
        act = action.get("action", "").lower()
        params = dict(action.get("params", {}))

        if tool_name == "file_manager" and act == "write":
            if "file_path" in params and "path" not in params:
                params["path"] = params.pop("file_path")
            file_path = params.get("path") or params.get("file_path")
            if file_path:
                intended_files.add(file_path)

        if tool_name == "file_manager" and act == "read":
            file_path = params.get("path") or params.get("file_path")
            if file_path:
                normalized = paths.normalize_path(file_path, project_root)
                if normalized not in existing_files:
                    msg = (
                        f"🔧 Omitiendo acción {idx+1}/{len(actions)}: "
                        f"{tool_name}.{act} (archivo '{file_path}' no existe)"
                    )
                    await add_system_message(msg)
                    execution_report.append(
                        f"- **{tool_name}.{act}** → omitido (archivo no encontrado)."
                    )
                    continue

        if project_root:
            params["project_root"] = project_root
        else:
            params.pop("project_root", None)

        await add_system_message(f"🔧 Ejecutando acción {idx+1}/{len(actions)}: {tool_name}.{act}")

        report_line, wrote, committed = await _execute_single_action(
            tool_name, act, params, workspace, project_root
        )
        execution_report.append(report_line)
        if wrote:
            files_written = True
            written_path = params.get("path") or params.get("file_path")
            if written_path:
                existing_files.add(written_path)
        if committed:
            commit_done = True

    return execution_report, files_written, commit_done, intended_files  # type: ignore[return-value]
