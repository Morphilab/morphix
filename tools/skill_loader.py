# tools/skill_loader.py
"""Tool load_skill — carga bajo demanda de skills procedimentales (superpowers-style)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.skills import SkillNotFoundError, SkillSummary, discover_skills, load_skill

if TYPE_CHECKING:
    from tools.registry import ToolsRegistry


async def _load_skill_tool(name: str = "", **_: object) -> str:
    """Devuelve el cuerpo markdown completo de una skill al contexto del agente."""
    if not name.strip():
        return "❌ load_skill requiere el parámetro 'name' (ver lista en <SKILLS>)."
    # Import perezoso: core.workspaces importa hacia arriba (agents/tools).
    from core.workspaces import get_global_workspaces

    workspace = get_global_workspaces().current
    try:
        skill = load_skill(name, workspace)
    except SkillNotFoundError:
        disponibles: list[SkillSummary] = discover_skills(workspace)
        nombres = ", ".join(s.name for s in disponibles) or "ninguna"
        return f"Error: la skill '{name}' no existe. Disponibles: {nombres}"
    # defensa en profundidad — el bootstrap ya oculta las
    # skills workspace no aprobadas; esto bloquea la llamada directa.
    if skill.origin == "workspace" and workspace:
        from core.skills_approval import approval_status

        if approval_status(workspace).get(skill.name) != "approved":
            return (
                f"🚫 La skill '{skill.name}' no está aprobada para este workspace "
                "(el usuario debe aprobarla desde la GUI antes de usarla)."
            )
    return f"# Skill cargada: {skill.name}\n\n{skill.body}"


def register(registry: ToolsRegistry | None = None) -> None:
    from tools.registry import tools_registry

    target = registry if registry is not None else tools_registry
    target.register("load_skill")(_load_skill_tool)


register()  # registro global al importar (convención del repo)
