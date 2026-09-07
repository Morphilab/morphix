"""Ambas variantes de nombre MCP pasan por el orquestador (hooks/budget)."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_colon_name_goes_through_orchestrator():
    called = {}

    async def fake_execute(tool_name, parameters, **kw):
        called["name"] = tool_name
        return {"success": True, "output": "ok"}

    # Cliente MCP dueño del prefijo 'browser' (si no, el fast-fail es correcto)
    from core.mcp import client as mcp_client_mod

    class FakeCfg:
        tools_prefix = "browser"

    class FakeClient:
        config = FakeCfg()

    orig = dict(mcp_client_mod._clients)
    mcp_client_mod._clients["fake_browser"] = FakeClient()
    try:
        with patch("tools.wrapper.tool_orchestrator") as orch:
            orch.execute_tool = AsyncMock(side_effect=fake_execute)
            from tools.wrapper import safe_tool_call

            result = await safe_tool_call("mcp:browser.navigate", {"url": "http://x"})
            orch.execute_tool.assert_awaited_once()
            assert called["name"] == "mcp:browser.navigate"
            assert result.get("success") is True
    finally:
        mcp_client_mod._clients.clear()
        mcp_client_mod._clients.update(orig)


@pytest.mark.asyncio
async def test_colon_name_without_client_fast_fails():
    """Fallback honesto: prefijo mcp: sin cliente dueño → error controlado."""
    with patch("tools.wrapper.tool_orchestrator") as orch:
        orch.execute_tool = AsyncMock(return_value={"success": True, "output": "x"})

        # Sin clientes MCP conectados (estado global vacío en tests)
        from core.mcp import client as mcp_client_mod

        orig = dict(mcp_client_mod._clients)
        mcp_client_mod._clients.clear()
        try:
            from tools.wrapper import safe_tool_call

            result = await safe_tool_call("mcp:nadie.tool", {})
        finally:
            mcp_client_mod._clients.clear()
            mcp_client_mod._clients.update(orig)

    assert result.get("success") is False
    assert result.get("error") == "mcp_client_not_found"
    orch.execute_tool.assert_not_awaited()
