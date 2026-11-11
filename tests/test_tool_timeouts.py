# tests/test_tool_timeouts.py — timeout code-scoped por tool
"""Defaults de timeout POR TOOL (bash/test_runner son lentos por naturaleza),
override explícito gana, y el payload de error lleva el código estable."""

from unittest.mock import patch

import pytest

from core.constants import TOOL_CALL_TIMEOUT_SECONDS
from tools.wrapper import safe_tool_call, tool_default_timeout


@pytest.mark.asyncio
async def test_bash_gets_longer_default_than_global():
    assert tool_default_timeout("bash_manager") > 60
    assert tool_default_timeout("memory_saver") == TOOL_CALL_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_explicit_timeout_wins_over_code_default(monkeypatch):
    captured = {}

    async def fake_exec(name, params, **kw):
        return {"success": True}

    async def fake_wait_for(coro, timeout):
        captured["timeout"] = timeout
        coro.close()
        return {"success": True}

    monkeypatch.setattr("tools.wrapper.tool_orchestrator.execute_tool", fake_exec)
    monkeypatch.setattr("tools.wrapper.asyncio.wait_for", fake_wait_for)

    await safe_tool_call("bash_manager", {"command": "ls"}, timeout=7)
    assert captured["timeout"] == 7


@pytest.mark.asyncio
async def test_timeout_payload_carries_taxonomy_code():
    """Al vencer el timeout, el resultado incluye code/tag estables."""

    async def hanging(*a, **k):
        import asyncio

        await asyncio.sleep(3600)

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise TimeoutError()

    with (
        patch("tools.wrapper.tool_orchestrator.execute_tool", hanging),
        patch("tools.wrapper.asyncio.wait_for", fake_wait_for),
    ):
        res = await safe_tool_call("code_search", {"query": "x"}, timeout=0.01)

    assert res["success"] is False
    assert res["error"] == "tool_timeout"
    assert res["code"] == "TOOL_TIMEOUT"
    assert res["tag"] == "Tool.Timeout"
