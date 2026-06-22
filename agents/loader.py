# core/agent_loader.py
import logging

import yaml

from agents.base import _execute_specialized_agent
from agents.registry import agents_registry

logger = logging.getLogger(__name__)


def load_workspace_agents(workspace_name: str):
    """Carga los agentes definidos en workspaces/<workspace>/agents/*.yaml"""
    from core.path_resolver import paths

    agents_dir = paths.workspace_agents_dir(workspace_name)
    if not agents_dir.exists():
        logger.info(f"No hay agentes locales en el workspace '{workspace_name}'.")
        return

    for agent_file in agents_dir.glob("*.yaml"):
        if agent_file.name.startswith("_"):
            continue  # plantillas (p.ej. _FULL_TEMPLATE.yaml) no son agentes
        try:
            with open(agent_file, encoding="utf-8") as f:
                profile = yaml.safe_load(f)
            if not profile or "name" not in profile:
                logger.warning(f"Archivo {agent_file} inválido, falta 'name'")
                continue

            name = profile["name"]

            # Solution: bind name value at definition time
            async def agent_func(
                task: str,
                history: list,
                pdf_text: str = "",
                tools_output: str = "",
                _name: str = name,
            ):
                return await _execute_specialized_agent(
                    _name, task, history, pdf_text, tools_output
                )

            agents_registry.register_workspace_agent(name, agent_func, profile)
            logger.info(f"Agente '{name}' cargado desde {agent_file}")
        except Exception as e:
            logger.error(f"Error cargando agente desde {agent_file}: {e}")


def unload_workspace_agents():
    agents_registry.clear_workspace_agents()
