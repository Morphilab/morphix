# tests/test_dsl_files_written_tool_steps.py
"""Los tool-steps del DSL registran sus escrituras en files_written."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from orchestration.dsl.runtime_adapter import ProductionRuntime


@pytest.mark.asyncio
async def test_call_tool_file_manager_write_registra_archivo():
    rt = ProductionRuntime(workspace="main", project_root="code_projects/demo")
    with patch("tools.wrapper.safe_tool_call", new_callable=AsyncMock) as mock_tool:
        mock_tool.return_value = {"success": True, "output": "ok"}
        await rt.call_tool(
            "file_manager",
            {"action": "write", "path": "tests/test_x.py", "content": "def test_x(): pass"},
        )
    assert rt.files_written == ["tests/test_x.py"]


@pytest.mark.asyncio
async def test_call_tool_write_fallido_no_registra():
    rt = ProductionRuntime(workspace="main")
    with patch("tools.wrapper.safe_tool_call", new_callable=AsyncMock) as mock_tool:
        mock_tool.return_value = {"success": False, "output": "denegado"}
        await rt.call_tool("file_manager", {"action": "write", "path": "x.py", "content": "x"})
    assert rt.files_written == []


@pytest.mark.asyncio
async def test_files_del_run_ve_archivo_de_tool_step():
    from orchestration.dsl.plugins import _files_del_run

    rt = ProductionRuntime(workspace="main")
    with patch("tools.wrapper.safe_tool_call", new_callable=AsyncMock) as mock_tool:
        mock_tool.return_value = {"success": True, "output": "ok"}
        await rt.call_tool(
            "file_manager", {"action": "write", "path": "tests/test_x.py", "content": "x"}
        )

    step = SimpleNamespace(params={"out": "tests_del_run", "glob": "tests/**/*.py"})
    state = SimpleNamespace(vars={})
    await _files_del_run(step, state, rt, "verificar")
    assert state.vars["tests_del_run"] == ["tests/test_x.py"]


@pytest.mark.asyncio
async def test_call_tool_normaliza_path_del_proyecto():
    """El path con prefijo del proyecto se guarda relativo: files_del_run lo matchea."""
    rt = ProductionRuntime(workspace="main", project_root="code_projects/demo")
    with patch("tools.wrapper.safe_tool_call", new_callable=AsyncMock) as mock_tool:
        mock_tool.return_value = {"success": True, "output": "ok"}
        await rt.call_tool(
            "file_manager",
            {
                "action": "write",
                "path": "code_projects/demo/tests/test_x.py",
                "content": "x",
            },
        )
    assert rt.files_written == ["tests/test_x.py"]
