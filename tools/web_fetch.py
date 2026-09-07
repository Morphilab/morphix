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


def _resolve_first_ip(hostname: str) -> str | None:
    """Resuelve UNA vez y retorna la primera IP (fail-closed None).

    El fetch posterior se ancla a esta IP (Host/SNI preservados) para cerrar
    la ventana DNS-rebinding entre el chequeo y la petición."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError):
        return None
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if not any(ip in net for net in _PRIVATE_NETWORKS):
            return str(ip)
    return None


def _pinned_url(url: str, ip: str) -> str:
    """Reescribe la URL apuntando a la IP literal (puerto y path intactos)."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host_part = f"[{ip}]" if ":" in ip else ip
    netloc = host_part
    if parsed.port:
        netloc = f"{host_part}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl()


_MAX_BODY_BYTES = 2 * 1024 * 1024  # cap ANTES de decodificar


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

    from urllib.parse import urlparse

    if await asyncio.to_thread(_is_private_url, url):
        return "❌ Acceso denegado: no se permiten URLs a redes internas/privadas."

    hostname = urlparse(url).hostname or ""
    pinned_ip = await asyncio.to_thread(_resolve_first_ip, hostname)
    if pinned_ip is None:
        return "❌ Acceso denegado: resolución DNS fallida o solo IPs privadas."

    headers_base = {"User-Agent": "Morphix/1.0", "Host": hostname}

    def _pin(u: str) -> str:
        return _pinned_url(u, pinned_ip)

    async def _get_pinned(client, target: str):
        """GET a la IP anclada preservando Host; SNI si el httpx lo soporta."""
        kwargs = {"headers": dict(headers_base), "follow_redirects": False}
        try:
            return await client.get(_pin(target), extensions={"sni_hostname": hostname}, **kwargs)
        except TypeError:  # httpx sin soporte de sni_hostname en extensions
            return await client.get(_pin(target), **kwargs)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = await _get_pinned(client, url)
            if resp.status_code in (301, 302, 303, 307, 308):
                for _ in range(5):  # max 5 redirects
                    redirect_url = resp.headers.get("location", "")
                    if not redirect_url:
                        break
                    if await asyncio.to_thread(_is_private_url, redirect_url):
                        return "❌ Acceso denegado: redirección a red interna/privada."
                    r_host = urlparse(redirect_url).hostname or ""
                    r_ip = await asyncio.to_thread(_resolve_first_ip, r_host)
                    if r_ip is None:
                        return "❌ Acceso denegado: redirección a host irresoluble/privado."
                    resp = await _get_pinned(client, redirect_url)
                    if resp.status_code not in (301, 302, 303, 307, 308):
                        break
                else:
                    return "❌ Demasiadas redirecciones al obtener la URL."
            if resp.status_code != 200:
                return f"❌ Error HTTP {resp.status_code} al obtener {url}"

            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return f"❌ Tipo de contenido no soportado: {content_type}"

            # leer por chunks con cap duro ANTES de decodificar — un body
            # gigante ya no se materializa completo en memoria.
            raw = bytearray()
            body_truncated = False
            async for chunk in resp.aiter_bytes(65536):
                raw.extend(chunk)
                if len(raw) > _MAX_BODY_BYTES:
                    raw = raw[:_MAX_BODY_BYTES]
                    body_truncated = True
                    break
            text = bytes(raw).decode(resp.encoding or "utf-8", errors="replace")

            # Basic HTML cleanup
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

            max_len = 10_000
            if len(text) > max_len:
                text = text[:max_len] + f"\n\n... (truncado a {max_len} caracteres)"

            if body_truncated:
                text += "\n\n... (body truncado a 2MB antes de decodificar)"
            log_operation("web_fetch", url[:200], success=True)
            return f"📄 {url}\n\n{text}"

    except httpx.TimeoutException:
        return f"⏱️ Timeout al obtener {url}"
    except Exception as e:
        logger.error(f"Web fetch error: {e}")
        return f"❌ Error al obtener {url}: {e!s}"


tools_registry.register("web_fetch")(_web_fetch_tool)
