# tests/test_mcp_trust.py — trust por workspace, islas y gate del llamante
"""Contratos de trust MCP:

- El registro ocurre al conectar: los servidores declarados por un
  workspace quedan trusted PARA ese workspace.
- Islas: el workspace B jamás adopta el trust de A para un mismo servidor.
- Gate en la llamada: rechazo JSON estable cuando el llamante con
  workspace explícito no tiene registro (fail-closed).
- El path sin workspace (legado global) no pasa por el gate.
- Ciclo de vida: disconnect limpia el registro junto a los clientes.
"""

import pytest

from core.mcp import client as mcp_client_mod
from core.mcp import trust


class _FakeCfg:
    def __init__(self, name, prefix):
        self.name = name
        self.tools_prefix = prefix


class _FakeClient:
    def __init__(self, name, prefix):
        self.config = _FakeCfg(name, prefix)


@pytest.fixture(autouse=True)
def _clean_state():
    trust.reset_trust()
    orig = dict(mcp_client_mod._clients)
    yield
    mcp_client_mod._clients.clear()
    mcp_client_mod._clients.update(orig)
    trust.reset_trust()


def _connect_fake(server="weather", prefix="weather"):
    cli = _FakeClient(server, prefix)
    mcp_client_mod._clients[server] = cli
    return cli


def test_record_and_query_trust():
    trust.record_server_trust("main", "weather")
    assert trust.is_server_trusted("main", "weather") is True
    assert trust.trusted_servers("main") == ["weather"]
    assert trust.is_server_trusted("otro", "weather") is False  # isla


def test_revoke_is_idempotent_and_reports():
    trust.record_server_trust("main", "weather")
    assert trust.revoke_server_trust("main", "weather") is True
    assert trust.is_server_trusted("main", "weather") is False
    assert trust.revoke_server_trust("main", "weather") is False  # ya no existía


def test_gate_blocks_workspace_without_record():
    _connect_fake()
    err = trust.trust_gate_check("otro-ws", "mcp:weather.forecast")
    assert err is not None
    assert err["success"] is False
    assert err["error"] == "mcp_untrusted_server"
    assert err["code"] == "MCP_UNTRUSTED"
    assert err["server"] == "weather"
    assert "otro-ws" in err["output"] and "weather" in err["output"]


def test_gate_allows_declaring_workspace():
    _connect_fake()
    trust.record_server_trust("main", "weather")
    assert trust.trust_gate_check("main", "mcp:weather.forecast") is None


def test_gate_ignores_non_mcp_and_unscoped_and_unresolvable():
    _connect_fake()
    assert trust.trust_gate_check("main", "file_manager") is None  # no-mcp
    assert trust.trust_gate_check(None, "mcp:weather.forecast") is None  # legado
    assert trust.trust_gate_check("main", "mcp:ghost.tool") is None  # sin dueño
    # ghost sin dueño caerá en el fast-fail de client_not_found del wrapper


async def test_island_semantics_through_wrapper(monkeypatch):
    """B no hereda aunque el cliente exista por A (hueco cross-workspace)."""
    _connect_fake()
    trust.record_server_trust("ws-a", "weather")

    from unittest.mock import AsyncMock, patch

    with patch("tools.wrapper.tool_orchestrator") as orch:
        orch.execute_tool = AsyncMock(return_value={"success": True, "output": "x"})
        from tools.wrapper import safe_tool_call

        blocked = await safe_tool_call("mcp:weather.forecast", {"city": "X"}, workspace="ws-b")
        assert blocked["success"] is False
        assert blocked["error"] == "mcp_untrusted_server"
        orch.execute_tool.assert_not_awaited()

        allowed = await safe_tool_call("mcp:weather.forecast", {"city": "X"}, workspace="ws-a")
        assert allowed["success"] is True
        orch.execute_tool.assert_awaited_once()


async def test_unscoped_legacy_path_not_gated():
    """Sin workspace el gate no aplica — path global histórico intacto."""
    _connect_fake()

    from unittest.mock import AsyncMock, patch

    with patch("tools.wrapper.tool_orchestrator") as orch:
        orch.execute_tool = AsyncMock(return_value={"success": True, "output": "ok"})
        from tools.wrapper import safe_tool_call

        result = await safe_tool_call("mcp:weather.forecast", {"city": "X"})
        assert result["success"] is True
        orch.execute_tool.assert_awaited_once()


async def test_connect_records_trust_per_workspace(monkeypatch):
    """El momento de registro: connect_mcp_servers ancla trust al workspace."""
    from core.mcp.config import MCPServerConfig

    cfg = MCPServerConfig(name="weather", command="/bin/true", tools_prefix="weather")

    async def fake_connect(self):
        return True

    monkeypatch.setattr(mcp_client_mod.MCPClient, "connect", fake_connect)
    import core.mcp.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_mcp_servers", lambda ws: [cfg] if ws == "ws-x" else [])

    await mcp_client_mod.connect_mcp_servers("ws-x")
    assert trust.is_server_trusted("ws-x", "weather") is True
    assert trust.is_server_trusted("ws-y", "weather") is False

    await mcp_client_mod.disconnect_mcp_servers()
    assert trust.trusted_servers("ws-x") == [], "disconnect limpia el registro"


async def test_server_for_tool_resolves_prefix():
    _connect_fake(server="alpha", prefix="a.b")
    assert trust.server_for_tool("mcp:a.b.tool") == "alpha"
    assert trust.server_for_tool("file_manager") is None


def test_server_for_tool_resuelve_nombre_sanitizado():
    """El nombre saneado (mcp_prefix_tool) también resuelve dueño y gate."""
    cli = _FakeClient("weather", "weather")
    mcp_client_mod._SPEC_OWNERS["mcp_weather_forecast"] = cli
    try:
        assert trust.server_for_tool("mcp_weather_forecast") == "weather"
        gate = trust.trust_gate_check("main", "mcp_weather_forecast")
        assert gate is not None
        assert gate["code"] == "MCP_UNTRUSTED"
        trust.record_server_trust("main", "weather")
        assert trust.trust_gate_check("main", "mcp_weather_forecast") is None
        assert mcp_client_mod.get_mcp_client_for_tool("mcp_weather_forecast") is cli
    finally:
        mcp_client_mod._SPEC_OWNERS.pop("mcp_weather_forecast", None)
