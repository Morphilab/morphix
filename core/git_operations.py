"""Git Operations — helper centralizado para auto-commit y operaciones git comunes.

Smart commit: generates the commit message via LLM based on the task.
"""

import logging

from tools.wrapper import safe_tool_call

logger = logging.getLogger(__name__)


async def auto_commit(
    workspace: str,
    project_root: str | None = None,
    message: str = "Auto-commit: tarea completada",
) -> dict:
    """Ejecuta git init, add -A, commit automático. Retorna {success, output}."""
    auto_params = {"workspace": workspace}
    if project_root:
        auto_params["project_root"] = project_root

    await safe_tool_call(
        tool_name="git_manager", parameters={"action": "init", **auto_params}, role="agent"
    )
    await safe_tool_call(
        tool_name="git_manager", parameters={"action": "add", **auto_params}, role="agent"
    )
    commit_res = await safe_tool_call(
        tool_name="git_manager",
        parameters={"action": "commit", "message": message, **auto_params},
        role="agent",
    )

    output = str(commit_res.get("output", "")) if isinstance(commit_res, dict) else str(commit_res)
    success = "Commit realizado" in output

    if success:
        logger.info("✅ Auto-commit: %s", message[:60])
    else:
        logger.warning("⚠️ Auto-commit fallido: %s", output[:200])

    return {"success": success, "output": output}


async def smart_auto_commit(
    workspace: str,
    project_root: str | None = None,
    task_description: str = "",
    files_written: list[str] | None = None,
) -> dict:
    """Commit automático con mensaje generado por LLM basado en la tarea.

    Si task_description está vacío, usa el mensaje por defecto.
    Si hay LLM disponible, genera un resumen de una línea de los cambios.
    """
    message = "Auto-commit: tarea completada"

    if task_description:
        try:
            message = await _generate_commit_message(task_description, files_written or [])
        except Exception:
            logger.debug("No se pudo generar mensaje de commit vía LLM, usando default")

    return await auto_commit(
        workspace=workspace,
        project_root=project_root,
        message=message,
    )


async def _generate_commit_message(task: str, files: list[str]) -> str:
    """Usa el LLM para generar un mensaje de commit descriptivo."""
    from llm import models

    files_list = "\n".join(f"- {f}" for f in files[:10]) if files else "No especificados"
    prompt = (
        f"Genera un mensaje de commit de Git de UNA SOLA LÍNEA (máximo 72 caracteres) "
        f"en español, en formato convencional (feat:, fix:, refactor:, test:, docs:, chore:).\n\n"
        f"Tarea realizada: {task[:300]}\n"
        f"Archivos modificados:\n{files_list}\n\n"
        "Solo responde con el mensaje de commit, sin explicaciones ni formato adicional."
    )

    try:
        response = await models.call(
            messages=[{"role": "user", "content": prompt}],
            role="fast",
            temperature=0.3,
        )
        msg = response.choices[0].message.content.strip()
        # Detect error responses from rate limiter / controller
        Error_INDICATORS = ["❌", "rate limit", "Rate limit", "Ollama también falló"]
        if any(indicator in msg for indicator in Error_INDICATORS):
            logger.warning(
                "_generate_commit_message received error response, using fallback: %s", msg[:80]
            )
            return f"feat: {task[:60]}"
        # Limpiar: quitar comillas, backticks, prefijos comunes
        msg = msg.replace("`", "").replace('"', "").replace("'", "")
        for prefix in ("Mensaje de commit:", "Commit message:", "commit:"):
            if msg.lower().startswith(prefix.lower()):
                msg = msg[len(prefix) :].strip()
        return msg[:72]
    except Exception:
        return f"feat: {task[:60]}"
