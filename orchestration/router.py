# features/maestro/services/agent_router.py
"""
Agent Router — selecciona el mejor agente para una tarea.
"""

import logging

from agents.registry import agents_registry
from core.config import settings
from core.lru_cache import LRUCache
from core.utils import clean_llm_response
from llm import models

logger = logging.getLogger(__name__)

_router_cache = LRUCache(max_size=500, ttl=300)


class AgentRouter:
    @staticmethod
    async def select_best_agent(
        task: str,
        primary_type: str = "mixed",
        allowed_agents: list[str] | None = None,
    ) -> str:
        cache_key = f"{primary_type}:{task}"

        cached = _router_cache.get(cache_key)
        if cached is not None:
            return cached

        # Obtener la lista actual de agentes disponibles
        available_agents = list(agents_registry.list_agents().keys())

        # Aplicar filtro si viene definido
        if allowed_agents is not None:
            available_agents = [a for a in available_agents if a in allowed_agents]
            if not available_agents:
                logger.warning("No hay agentes permitidos. Usando fallback 'conversacional'.")
                return settings.fallback_agent

        agent_list_str = ", ".join(available_agents)

        prompt = f"""You are a precise task router.
Respond ONLY with one exact word from this list:

{agent_list_str}

Task: "{task}"

Context: {primary_type.upper()}

Choose the best agent for this task. Reply with ONLY the agent name, nothing else."""

        try:
            response = await models.call(
                messages=[{"role": "user", "content": prompt}],
                role="fast",
                temperature=0.0,
            )

            text = clean_llm_response(response).lower().strip()

            # Check if the response matches any registered agent
            for agent in available_agents:
                if agent in text:
                    logger.info(f"AgentRouter → {agent.upper()} para: {task[:70]}...")
                    _router_cache.set(cache_key, agent)
                    return agent

            # Fallback: if not recognized, use the first available agent
            fallback = available_agents[0]
            logger.warning(f"⚠️ AgentRouter no pudo decidir, usando fallback: {fallback}")
            return fallback

        except Exception as e:
            logger.error(f"Error en AgentRouter: {e}")
            return available_agents[0] if available_agents else "conversacional"


agent_router = AgentRouter()
