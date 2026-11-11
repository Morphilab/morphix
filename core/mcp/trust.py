# core/mcp/trust.py — trust por workspace: el secundario jamás hereda
"""Registro de confianza MCP anclado al workspace que DECLARÓ el servidor.

El trust se ancla al workspace que declaró el servidor: un perfil
secundario jamás hereda el trust del perfil lanzador para un servidor con
el mismo nombre.

Contrato:

- **Momento de registro** = ``connect_mcp_servers(workspace)``: los
  servidores que la config de ESE workspace declara quedan trusted para
  ESE workspace (``core/workspaces.py`` conecta al cambiar de workspace).
- **Gate en la llamada** = ``tools/wrapper.safe_tool_call``: cuando la
  sesión trae workspace explícito (bots, workflows), el gate resuelve el
  servidor dueño del prefijo ``mcp:<prefix>.<tool>`` y exige registro
  para ESE workspace. Sin registro → fail-closed con error JSON estable.
- **Islas**: los registros son ``(workspace, servidor)`` — el workspace B
  nunca ve los de A, aunque compartan proceso y clientes conectados.
- **Legado no roto**: llamadas sin workspace (path global histórico) no
  pasan por el gate — el único hueco existente es cross-workspace cuando el
  caller SÍ trae workspace.

El registro vive en memoria del proceso, emparejado al ciclo de vida de
``_clients`` (connect registra, disconnect limpia): sin sidecar, sin TTL
— el dict es O(1). Revocación explícita vía :func:`revoke_server_trust`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# (workspace, server_name) → concedido. La ausencia de la clave es
# "desconocido" y el gate la trata como NO confiable (fail-closed).
_TRUST: dict[tuple[str, str], None] = {}


def record_server_trust(workspace: str, server_name: str) -> None:
    """Concede trust a un servidor PARA un workspace (momento connect)."""
    _TRUST[(workspace, server_name)] = None
    logger.debug("mcp-trust: '%s' confía en '%s'", workspace, server_name)


def revoke_server_trust(workspace: str, server_name: str) -> bool:
    """Revoca el trust de un workspace hacia un servidor.

    Devuelve True si existía el registro. Idempotente.
    """
    existed = (workspace, server_name) in _TRUST
    _TRUST.pop((workspace, server_name), None)
    return existed


def is_server_trusted(workspace: str, server_name: str) -> bool:
    return (workspace, server_name) in _TRUST


def trusted_servers(workspace: str) -> list[str]:
    return sorted(server for ws, server in _TRUST if ws == workspace)


def reset_trust() -> None:
    """Limpia todo el registro (ciclo de vida de _clients / tests)."""
    _TRUST.clear()


def server_for_tool(tool_name: str) -> str | None:
    """Resuelve el servidor dueño de un nombre MCP (colon o sanitizado)."""
    if tool_name.startswith("mcp:"):
        prefix = tool_name[4:].rsplit(".", 1)[0] if "." in tool_name else tool_name[4:]
        from core.mcp.client import _clients

        for client in _clients.values():
            if getattr(client.config, "tools_prefix", None) == prefix:
                return getattr(client.config, "name", None)
        return None
    if tool_name.startswith("mcp_"):
        from core.mcp.client import _SPEC_OWNERS

        owner = _SPEC_OWNERS.get(tool_name)
        return getattr(owner.config, "name", None) if owner is not None else None
    return None


def trust_gate_check(workspace: str | None, tool_name: str) -> dict | None:
    """Gate del llamante: error JSON si el workspace no confía en el servidor.

    El wrapper solo consulta el gate con workspace EXPLÍCITO (bots,
    workflows): el workspace activo por defecto usa los servidores que ÉL
    declaró al conectar (registro en :func:`record_server_trust`), igual que
    — sin regresión para el flujo del operador. El hueco que cierra es
    el cross-workspace: una sesión con workspace propio jamás hereda el
    trust del workspace que conectó los servidores.

    Devuelve None cuando la llamada puede seguir (no-mcp, sin workspace
    explícito, servidor irresoluble —el fast-fail de cliente manda— o
    trust registrado). El payload de rechazo usa claves estables para que
    el modelo y las superficies lo rendericen sin parsear prosa.
    """
    if not workspace or not tool_name.startswith(("mcp:", "mcp_")):
        return None
    server = server_for_tool(tool_name)
    if server is None:
        return None  # sin cliente dueño: el fast-fail de client_not_found aplica
    if is_server_trusted(workspace, server):
        return None
    logger.info(
        "mcp-trust: '%s' bloqueado — workspace '%s' no confía en '%s'",
        tool_name,
        workspace,
        server,
    )
    return {
        "success": False,
        "error": "mcp_untrusted_server",
        "code": "MCP_UNTRUSTED",
        "server": server,
        "output": (
            f"❌ El workspace '{workspace}' no confía en el servidor MCP "
            f"'{server}'. Decláralo en la config del workspace o concede "
            "trust explícito antes de usarlo."
        ),
    }
