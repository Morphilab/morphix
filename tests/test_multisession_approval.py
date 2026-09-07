# tests/test_multisession_approval.py
"""Multi-sesión: el callback de approval es per-run (ContextVar), no global.

Dos workflows concurrentes deben ver cada uno SU callback de aprobación, sin
que el `finally` de uno deje al otro sin aprobaciones (bug last-wins + clear
prematuro documentado en tools/orchestrator.py).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_settings():
    s = MagicMock()
    s.tools_enabled = True
    s.hooks_enabled = False
    s.tool_max_retries = 1
    s.tool_backoff_base = 0.0
    s.tool_max_tokens_per_workflow = 100
    s.tool_enable_token_budget = False
    s.active_workspace = "test_ws"
    return s


@pytest.mark.asyncio
async def test_approval_callback_is_per_run_context(monkeypatch):
    """Dos runs concurrentes ven cada uno su propio callback (sin pisarse)."""
    from tools.orchestrator import (
        ToolOrchestrator,
        clear_approval_callback,
        get_approval_callback,
        set_approval_callback,
    )

    monkeypatch.setattr(ToolOrchestrator, "on_approval_required", None, raising=False)

    seen: dict[str, bool] = {}

    async def cb_a(tool: str, params: dict) -> bool:
        seen["a"] = True
        return False

    async def cb_b(tool: str, params: dict) -> bool:
        seen["b"] = True
        return False

    async def run(tag: str, cb) -> dict:
        set_approval_callback(cb)
        try:
            # cede el control para que ambas tasks seteen su callback antes
            # de ejecutar la tool peligrosa.
            import asyncio

            await asyncio.sleep(0)
            assert get_approval_callback() is cb
            return await ToolOrchestrator.execute_tool(
                tool_name="diff_editor",
                parameters={"action": "apply", "file_path": f"{tag}.txt", "diff_content": "x"},
                workspace="test_ws",
            )
        finally:
            clear_approval_callback()

    tool_func = AsyncMock(return_value={"success": True, "output": "ok"})

    with (
        patch("tools.orchestrator.settings", _mock_settings()),
        patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func),
    ):
        import asyncio

        results = await asyncio.gather(run("a", cb_a), run("b", cb_b))

    assert seen.get("a") is True
    assert seen.get("b") is True
    assert results[0]["error"] == "approval_denied"
    assert results[1]["error"] == "approval_denied"
    assert tool_func.call_count == 0


@pytest.mark.asyncio
async def test_clear_one_run_does_not_affect_other(monkeypatch):
    """El `finally` del run A (clear) no deja sin callback al run B."""
    from tools.orchestrator import (
        ToolOrchestrator,
        clear_approval_callback,
        get_approval_callback,
        set_approval_callback,
    )

    monkeypatch.setattr(ToolOrchestrator, "on_approval_required", None, raising=False)

    async def cb_b(tool: str, params: dict) -> bool:
        return False

    b_state: dict = {}

    async def run_a():
        set_approval_callback(None)
        import asyncio

        await asyncio.sleep(0)
        clear_approval_callback()

    async def run_b():
        set_approval_callback(cb_b)
        import asyncio

        await asyncio.sleep(0.02)
        b_state["cb_after_a_cleared"] = get_approval_callback()
        clear_approval_callback()

    import asyncio

    await asyncio.gather(run_a(), run_b())

    assert b_state["cb_after_a_cleared"] is cb_b
