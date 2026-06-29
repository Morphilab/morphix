# features/maestro/services/supervisor.py
"""
WorkflowSupervisor - Versión Dinámica basada en perfiles de agentes
"""

import logging

from agents.registry import agents_registry
from core.config import settings

logger = logging.getLogger(__name__)


class WorkflowSupervisor:
    @staticmethod
    async def review_and_correct(
        task_analyzer_result: dict,
        router_selections: list[str],
        subtasks: list[str],
        allowed_agents: list[str] | None = None,
    ) -> list[str]:
        """
        Review and correct agent selections based on keyword matching.
        Controlled by AUTO_FIX_LEVEL: 0=report only, 1=flag, 2=auto-correct.
        """
        fix_level = settings.auto_fix_level

        # Level 0: no review at all — return selections as-is
        if fix_level == 0:
            logger.debug("AUTO_FIX_LEVEL=0 — skipping supervisor review")
            return router_selections

        # Get all available agents with their keywords
        all_agents = list(agents_registry.list_agents().keys())

        # Filtrar si hay lista blanca
        if allowed_agents is not None:
            all_agents = [a for a in all_agents if a in allowed_agents]
            if not all_agents:
                logger.warning(
                    "No hay agentes permitidos. Se conservan las selecciones del router."
                )
                return router_selections

        agent_keywords = {}
        for agent_name in all_agents:
            profile = agents_registry.get_profile(agent_name)
            if profile:
                agent_keywords[agent_name] = profile.get("keywords", [])

        corrected = router_selections.copy()

        # Ensure corrected has at least as many entries as subtasks
        while len(corrected) < len(subtasks):
            corrected.append(all_agents[0] if all_agents else settings.fallback_agent)

        for i, task in enumerate(subtasks):
            task_lower = task.lower()

            # Preserve analyst for verification tasks (read-only, should not modify files)
            router_sel = router_selections[i] if i < len(router_selections) else ""
            if router_sel == "analista" and any(
                kw in task_lower
                for kw in (
                    "verificar",
                    "validar",
                    "revisar",
                    "probar",
                    "test",
                    "prueba",
                    "comprobar",
                )
            ):
                continue  # keep router selection — analyst is correct for verification

            best_agent = None
            best_score = -1

            # Evaluate each agent by keyword match
            for agent, keywords in agent_keywords.items():
                if not keywords:
                    continue
                score = sum(1 for kw in keywords if kw in task_lower)
                if score > best_score:
                    best_score = score
                    best_agent = agent

            # If an agent with matches was found, use it
            if best_agent is not None and best_score > 0:
                corrected[i] = best_agent
            else:
                # If no matches, keep the router decision (which is already valid)
                # If the router returned something unregistered, use the first allowed agent
                router_sel = router_selections[i] if i < len(router_selections) else ""
                if router_sel not in all_agents:
                    corrected[i] = all_agents[0] if all_agents else settings.fallback_agent

        logger.info(f"✅ Supervisor finalizó → Agentes corregidos: {corrected}")

        # Level 1: flag issues but return original selections
        if fix_level == 1:
            for i in range(len(corrected)):
                router_sel = router_selections[i] if i < len(router_selections) else ""
                subtask_text = subtasks[i][:50] if i < len(subtasks) else ""
                if i < len(router_selections) and corrected[i] != router_selections[i]:
                    logger.info(
                        f"🔍 Supervisor flag (fix_level=1): subtask {i} "
                        f"'{subtask_text}...' would change "
                        f"from '{router_sel}' to '{corrected[i]}'"
                    )
            return router_selections

        return corrected
