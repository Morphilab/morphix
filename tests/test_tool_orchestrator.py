# tests/test_tool_orchestrator.py
"""Tests para el orchestrator de herramientas y su fast-fail."""

from unittest.mock import AsyncMock, patch

import pytest


class TestResultBasedFastFail:
    @pytest.mark.asyncio
    async def test_file_not_found_skips_retry(self):
        """File-not-found en result-based failure no debe reintentar."""
        from tools.orchestrator import ToolOrchestrator

        tool_func = AsyncMock()
        tool_func.return_value = {
            "success": False,
            "output": "Archivo no encontrado: app.py",
        }

        with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
            result = await ToolOrchestrator.execute_tool(
                tool_name="test_runner",
                parameters={"file_path": "app.py"},
                workspace="test_ws",
            )

        assert result["success"] is False
        assert tool_func.call_count == 1

    @pytest.mark.asyncio
    async def test_other_failure_still_retries(self):
        """Errores que no son file-not-found sí reintentan."""
        from tools.orchestrator import ToolOrchestrator

        tool_func = AsyncMock()
        tool_func.return_value = {
            "success": False,
            "output": "Syntax error in test execution",
        }

        with patch("tools.orchestrator.tools_registry.get_tool", return_value=tool_func):
            result = await ToolOrchestrator.execute_tool(
                tool_name="test_runner",
                parameters={"file_path": "test.py"},
                workspace="test_ws",
            )

        assert result["success"] is False
        assert tool_func.call_count >= 2
