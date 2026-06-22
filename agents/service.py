# features/agents/services/agents_service.py
import logging

from core.config import settings
from llm import models

logger = logging.getLogger(__name__)


class AgentsService:
    """Servicio centralizado para ejecutar agentes especializados."""

    @staticmethod
    def get_available_agents():
        """Devuelve los 6 agentes especializados (nombres exactos)."""
        from agents.registry import agents_registry

        return list(agents_registry.list_agents().keys())

    @staticmethod
    async def execute_agent(agent_type: str, query: str, history: list, on_stream_chunk=None):
        """Ejecuta cualquier agente registrado."""
        try:
            from agents.registry import agents_registry

            agent_func = agents_registry.get_agent(agent_type)
            if not agent_func:
                return f"❌ Agent '{agent_type}' no encontrado."

            if on_stream_chunk:
                from agents.base import _execute_specialized_agent

                return await _execute_specialized_agent(
                    agent_type, query, history, on_stream_chunk=on_stream_chunk
                )
            return await agent_func(query, history)
        except Exception as e:
            logger.error(f"Error ejecutando agent {agent_type}: {e}")
            return f"❌ Error en el agente {agent_type}: {str(e)[:200]}"

    @staticmethod
    async def categorize_task(task: str) -> str:
        """Classify task using available agents from the registry."""
        try:
            from agents.registry import agents_registry

            available = list(agents_registry.list_agents().keys())
            agent_list = ", ".join(available)

            prompt = (
                "Classify this task into one exact word from this list: "
                f"{agent_list}. Reply ONLY with the word in lowercase.\n\n"
                f"Task: {task}"
            )

            response = await models.call(
                messages=[{"role": "user", "content": prompt}],
                role="fast",
                temperature=0.0,
                max_tokens=20,
            )

            category = response.choices[0].message.content.strip().lower()
            return category if category in available else settings.fallback_agent

        except Exception:
            logger.warning("Fallback in categorize_task")
            return settings.fallback_agent
