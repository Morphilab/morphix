# tools/delegation.py — superficie de tool del motor de delegación
"""delegate_task: el modelo delega subtareas a subagentes en caliente.

Handler fino sobre :mod:`orchestration.delegation` — el motor (roles,
units, schema, interrupt) vive en orquestación; aquí solo parametrización
y serialización. El workspace/sesión llegan por el contexto ambiente del
orquestador; los callers programáticos (workflows, bots) usan el motor
directo para llevar session_id (interrupt fan-out).

Auto-registro con el mismo patrón que memory_inspector/test_runner.
"""

import logging

logger = logging.getLogger(__name__)


def make_handler():
    async def execute(
        goal: str = "",
        context: str = "",
        agent_type: str = "",
        tasks: str = "",
        background: bool = False,
        output_schema: str = "",
        workspace: str = "",
        project_root: str = "",
        allowed_tools: list[str] | None = None,
    ) -> dict:
        import json as _json

        from orchestration.delegation import delegate_task

        if background:
            return {
                "success": False,
                "error": (
                    "background no disponible: usa foreground; el seguimiento "
                    "llega con la fase de delegación"
                ),
            }

        kwargs: dict = {"goal": goal or None, "context": context}
        if agent_type:
            kwargs["agent_type"] = agent_type
        if tasks:
            try:
                kwargs["tasks"] = _json.loads(tasks)
            except _json.JSONDecodeError as exc:
                return {"success": False, "error": f"tasks JSON inválido: {exc}"}
        if output_schema:
            try:
                kwargs["output_schema"] = _json.loads(output_schema)
            except _json.JSONDecodeError as exc:
                return {"success": False, "error": f"output_schema JSON inválido: {exc}"}
        if workspace:
            kwargs["workspace"] = workspace
        if project_root:
            kwargs["project_root"] = project_root
        if allowed_tools is not None:
            kwargs["allowed_tools"] = list(allowed_tools)
        try:
            result = await delegate_task(**kwargs)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        if isinstance(result, dict):
            return result
        return {"success": False, "error": "resultado inesperado del motor"}

    return execute


def register(registry):
    registry.register("delegate_task")(make_handler())


# Auto-registro (mismo patrón que memory_inspector/test_runner)
from tools.registry import tools_registry  # noqa: E402

register(tools_registry)  # noqa: E402
