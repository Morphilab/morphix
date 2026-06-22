# core/agents_registry.py
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class AgentsRegistry:
    """Registro de agentes — instanciable, no singleton.

    La instancia global legacy 'agents_registry' se mantiene para backward compat.
    Para nuevos entry points o tests, crear AgentsRegistry() directamente.
    """

    def __init__(self):
        self._global_agents: dict[str, Callable] = {}
        self._global_profiles: dict[str, dict] = {}
        self._workspace_agents: dict[str, Callable] = {}
        self._workspace_profiles: dict[str, dict] = {}

    # ---------- Registro global ----------
    def register_global(self, agent_type: str, profile: dict | None = None):
        def decorator(func: Callable) -> Callable:
            normalized = agent_type.lower()
            if normalized in self._global_agents:
                logger.warning(f"Agent global '{normalized}' ya registrado – sobrescribiendo.")
            self._global_agents[normalized] = func
            if profile:
                self._global_profiles[normalized] = profile
            logger.info(f"Agent global registrado: {normalized}")
            return func

        return decorator

    # ---------- Registro de workspace ----------
    def register_workspace_agent(
        self, agent_type: str, func: Callable, profile: dict | None = None
    ):
        normalized = agent_type.lower()
        self._workspace_agents[normalized] = func
        if profile:
            self._workspace_profiles[normalized] = profile
        logger.info(f"Agent workspace registrado: {normalized}")

    def clear_workspace_agents(self):
        self._workspace_agents.clear()
        self._workspace_profiles.clear()
        logger.info("Agentes del workspace descargados")

    # ---------- Consulta ----------
    def get_agent(self, agent_type: str) -> Callable | None:
        normalized = agent_type.lower()
        if normalized in self._workspace_agents:
            return self._workspace_agents[normalized]
        return self._global_agents.get(normalized)

    def get_profile(self, agent_type: str) -> dict | None:
        normalized = agent_type.lower()
        if normalized in self._workspace_profiles:
            return self._workspace_profiles[normalized]
        return self._global_profiles.get(normalized)

    def list_agents(self) -> dict[str, Callable]:
        return {**self._global_agents, **self._workspace_agents}

    def list_global_agents(self) -> dict[str, Callable]:
        return self._global_agents.copy()

    def clear(self):
        self._global_agents.clear()
        self._global_profiles.clear()
        self._workspace_agents.clear()
        self._workspace_profiles.clear()


# Legacy global instance — compatibility with existing code.
agents_registry = AgentsRegistry()
