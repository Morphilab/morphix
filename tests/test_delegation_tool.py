# tests/test_delegation_tool.py
"""Wiring de la tool delegate_task: contexto, allowlist y background honesto."""

from unittest.mock import AsyncMock, patch

import pytest

from tools.delegation import make_handler


@pytest.mark.asyncio
async def test_handler_propaga_contexto_al_engine():
    handler = make_handler()
    with patch("orchestration.delegation.delegate_task", new_callable=AsyncMock) as mock_dt:
        mock_dt.return_value = {"success": True, "units": []}
        await handler(
            goal="arregla el bug",
            workspace="main",
            project_root="code_projects/demo",
            allowed_tools=["file_manager", "delegate_task"],
        )
    kwargs = mock_dt.call_args.kwargs
    assert kwargs["workspace"] == "main"
    assert kwargs["project_root"] == "code_projects/demo"
    assert kwargs["allowed_tools"] == ["file_manager", "delegate_task"]


def test_inject_context_kwargs_inyecta_allowed_tools():
    import tools.delegation  # noqa: F401 — registra el handler real
    from orchestration.loop import _inject_context_kwargs

    out = _inject_context_kwargs(
        "delegate_task",
        {"goal": "x"},
        project_root="code_projects/demo",
        workspace="main",
        allowed_tools=["file_manager"],
    )
    assert out["allowed_tools"] == ["file_manager"]
    assert out["project_root"] == "code_projects/demo"


@pytest.mark.asyncio
async def test_handler_rechaza_background_con_mensaje_accionable():
    with patch("orchestration.delegation.delegate_task", new_callable=AsyncMock) as mock_dt:
        mock_dt.return_value = {"success": True, "units": []}
        res = await make_handler()(goal="x", background=True)
    assert res["success"] is False
    assert "background" in res["error"].lower()
    mock_dt.assert_not_awaited()
