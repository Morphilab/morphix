"""Clarification tool — allows agents to ask the user questions mid-workflow."""

import logging

logger = logging.getLogger(__name__)


async def _ask_clarification_tool(
    question: str,
    options: list[str] | None = None,
) -> dict:
    """Ask the user a clarification question and pause the workflow.

    The workflow is paused until the user responds. This tool is intercepted
    by the agent loop before normal execution — it never executes as a regular tool.

    Args:
        question: The question to ask the user.
        options: Optional list of choices for the user.
    """
    return {
        "success": True,
        "question": question,
        "options": options or [],
        "output": f"⏸️ Pausa para clarificar: {question}",
    }


from tools.registry import tools_registry

tools_registry.register("ask_clarification")(_ask_clarification_tool)
