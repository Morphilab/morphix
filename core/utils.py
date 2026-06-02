# core/utils.py
"""
Utilidades generales de Morphix
"""

import logging
import re

logger = logging.getLogger(__name__)


from typing import Any


def clean_llm_response(response: Any) -> str:
    """Limpieza ULTRA-agresiva de respuestas del LLM.
    Versión canónica — fusiona la detección de coroutine (memory/manager.py)
    y el regex eval_count (workflow_utils.py)."""
    if hasattr(response, "__await__"):
        logger.error("⚠️ Se detectó un coroutine sin await en clean_llm_response")
        return "[ERROR INTERNO: llamada async sin await]"

    try:
        if hasattr(response, "choices") and response.choices:
            content = response.choices[0].message.content.strip()
        elif hasattr(response, "message") and hasattr(response.message, "content"):
            content = response.message.content.strip()
        else:
            content = str(response)

        content = re.sub(r"model=.*?(?=content=)", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"created_at=.*?(?=\n|$)", "", content, flags=re.DOTALL)
        content = re.sub(r"thinking=.*?(?=\n|$)", "", content, flags=re.DOTALL)
        content = re.sub(r"total_duration=.*?(?=\n|$)", "", content, flags=re.DOTALL)
        content = re.sub(r"eval_count=.*?(?=\n|$)", "", content, flags=re.DOTALL)

        match = re.search(r"content='([\s\S]*?)'", content)
        if match:
            content = match.group(1).strip()

        return content.strip()
    except (AttributeError, TypeError, KeyError):
        return str(response)[:800]
