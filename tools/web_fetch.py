"""Web Fetch — obtiene y convierte páginas web a texto."""

import asyncio
import ipaddress
import logging
import re
import socket
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

# Hostnames que resuelven (o codifican) direcciones internas sin DNS externo:
# localhost y sufijos de redes caseras/corporativas + servicios de rebinding
# DNS (nip.io, sslip.io, xip.io) que codifican la IP en el nombre.
_PRIVATE_HOSTNAME_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".lan",
    ".home.arpa",
    ".nip.io",
    ".sslip.io",
    ".xip.io",
)


def _is_private_hostname(hostname: str) -> bool:
    """Bloquea hostnames de redes internas o rebinding sin consultar DNS."""
    lowered = hostname.lower().rstrip(".")
    if lowered == "localhost":
        return True
    return lowered.endswith(_PRIVATE_HOSTNAME_SUFFIXES)


def _resolves_to_private_ip(hostname: str) -> bool:
    """Resuelve el hostname y verifica si alguna IP resultante es privada.

    Fail-closed: si la resolución falla, se considera privada (no se puede
    demostrar que sea pública).
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError):
        return True
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if any(addr in net for net in _PRIVATE_NETWORKS):
            return True
    return False


def _is_private_url(url: str) -> bool:
    """Verifica si la URL apunta a una IP privada/internal (SSRF protection).

    Cubre: IPs literales privadas, hostnames internos (localhost, *.internal),
    servicios de rebinding (nip.io, sslip.io, xip.io) y hostnames que resuelven
    a IPs privadas (verificación post-DNS).
    """
    hostname = urlparse(url).hostname
    if not hostname:
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        pass
    if _is_private_hostname(hostname):
        return True
    return _resolves_to_private_ip(hostname)


async def _web_fetch_tool(url: str, **kwargs) -> str:
    """Obtiene el contenido de una URL y lo devuelve como texto.

    Args:
        url: URL a obtener.

    Returns:
        Contenido de la página en texto plano (HTML tags removidos).
    """
    if not url.startswith(("http://", "https://")):
        return "❌ URL inválida: debe comenzar con http:// o https://"

    if await asyncio.to_thread(_is_private_url, url):
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
                    if await asyncio.to_thread(_is_private_url, redirect_url):
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
