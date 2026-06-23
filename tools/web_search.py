"""Web Search — búsqueda web via Google Custom Search API."""

import logging

import httpx

from agents.audit import log_operation
from core.config import settings
from tools.registry import tools_registry

logger = logging.getLogger(__name__)


async def _web_search_tool(query: str, num: int = 5, **kwargs) -> str:
    """Busca en la web usando Google Custom Search API.

    Args:
        query: Términos de búsqueda.
        num: Número de resultados (máx 10).

    Returns:
        Resultados formateados con título, snippet, y URL.
    """
    api_key = settings.google_api_key
    cx = settings.google_cx

    if not api_key or not cx:
        return "❌ Google Search no configurado. Define GOOGLE_API_KEY y GOOGLE_CX en .env"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": api_key, "cx": cx, "q": query, "num": min(num, 10)},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if "items" not in data:
                return f"🔍 Sin resultados para: {query}"

            lines = [f"🔍 Resultados para: {query}\n"]
            for i, item in enumerate(data["items"], 1):
                title = item.get("title", "Sin título")
                snippet = item.get("snippet", "")
                link = item.get("link", "")
                lines.append(f"**{i}. {title}**\n{snippet}\n{link}\n")

            log_operation("web_search", query[:200], success=True)
            return "\n".join(lines)

    except Exception as e:
        logger.error(f"Web search error: {e}")
        return f"❌ Error en búsqueda web: {e!s}"


tools_registry.register("web_search")(_web_search_tool)
