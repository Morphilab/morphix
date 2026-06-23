"""Web Fetch — obtiene y convierte páginas web a texto."""

import ipaddress
import logging
import re
from urllib.parse import urlparse

import httpx

from agents.audit import log_operation
from tools.registry import tools_registry

logger = logging.getLogger(__name__)

# Private/reserved IP ranges (RFC 1918, RFC 6598, RFC 6890, loopback, link-local)
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6
    ipaddress.ip_network("2002::/16"),  # 6to4 tunnel
    ipaddress.ip_network("::/128"),  # Unspecified
]


def _is_private_url(url: str) -> bool:
    """Verifica si la URL apunta a una IP privada/internal (SSRF protection)."""
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return True
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        # Not an IP (hostname) — trust public DNS
        return False


async def _web_fetch_tool(url: str, **kwargs) -> str:
    """Obtiene el contenido de una URL y lo devuelve como texto.

    Args:
        url: URL a obtener.

    Returns:
        Contenido de la página en texto plano (HTML tags removidos).
    """
    if not url.startswith(("http://", "https://")):
        return "❌ URL inválida: debe comenzar con http:// o https://"

    if _is_private_url(url):
        return "❌ Acceso denegado: no se permiten URLs a redes internas/privadas."

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Morphix/1.0"},
                follow_redirects=False,
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                for _ in range(5):  # max 5 redirects
                    redirect_url = resp.headers.get("location", "")
                    if not redirect_url:
                        break
                    if _is_private_url(redirect_url):
                        return "❌ Acceso denegado: redirección a red interna/privada."
                    resp = await client.get(
                        redirect_url,
                        headers={"User-Agent": "Morphix/1.0"},
                        follow_redirects=False,
                    )
                    if resp.status_code not in (301, 302, 303, 307, 308):
                        break
                else:
                    return "❌ Demasiadas redirecciones al obtener la URL."
            if resp.status_code != 200:
                return f"❌ Error HTTP {resp.status_code} al obtener {url}"

            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return f"❌ Tipo de contenido no soportado: {content_type}"

            text = resp.text

            # Basic HTML cleanup
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

            max_len = 10_000
            if len(text) > max_len:
                text = text[:max_len] + f"\n\n... (truncado a {max_len} caracteres)"

            log_operation("web_fetch", url[:200], success=True)
            return f"📄 {url}\n\n{text}"

    except httpx.TimeoutException:
        return f"⏱️ Timeout al obtener {url}"
    except Exception as e:
        logger.error(f"Web fetch error: {e}")
        return f"❌ Error al obtener {url}: {e!s}"


tools_registry.register("web_fetch")(_web_fetch_tool)
