# tests/test_plan_executor.py
"""Tests para el ejecutor de planes de acciones."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.executor.plan import (
    _execute_plan_actions,
    _resolve_agent_and_task,
    _verify_file_written,
)


class TestResolveAgentAndTask:
    @pytest.mark.asyncio
    async def test_uses_forced_agent_when_valid(self):
        registry = MagicMock()
        registry.list_agents.return_value = ["tecnico", "analista"]
        agent, task = await _resolve_agent_and_task("crear archivo", [], "tecnico", registry)
        assert agent == "tecnico"

    @pytest.mark.asyncio
    async def test_falls_back_to_router_when_forced_invalid(self):
        registry = MagicMock()
        registry.list_agents.return_value = ["tecnico"]
        with patch(
            "orchestration.router.agent_router.select_best_agent",
            new_callable=AsyncMock,
        ) as mock_router:
            mock_router.return_value = "tecnico"
            agent, task = await _resolve_agent_and_task(
                "crear archivo", [], "inexistente", registry
            )
            mock_router.assert_awaited_once()
            assert agent == "tecnico"

    @pytest.mark.asyncio
    async def test_does_not_add_test_hint(self):
        """With suffix removed, task should remain unchanged."""
        registry = MagicMock()
        registry.list_agents.return_value = ["tecnico"]
        with patch(
            "orchestration.router.agent_router.select_best_agent",
            new_callable=AsyncMock,
        ) as mock_router:
            mock_router.return_value = "tecnico"
            _, task = await _resolve_agent_and_task(
                "crear app",
                [{"role": "user", "content": "hola"}],
                "tecnico",
                registry,
            )
            assert task == "crear app"

    @pytest.mark.asyncio
    async def test_does_not_add_test_hint_for_test_tasks(self):
        registry = MagicMock()
        registry.list_agents.return_value = ["tecnico"]
        with patch(
            "orchestration.router.agent_router.select_best_agent",
            new_callable=AsyncMock,
        ) as mock_router:
            mock_router.return_value = "tecnico"
            _, task = await _resolve_agent_and_task(
                "escribe un test",
                [{"role": "user", "content": "hola"}],
                "tecnico",
                registry,
            )
            assert "pytest" not in task


class TestVerifyFileWritten:
    @pytest.mark.asyncio
    async def test_returns_success_when_file_exists(self):
        with patch(
            "tools.file_manager.FileManager.execute",
            new_callable=AsyncMock,
        ):
            result = await _verify_file_written({"path": "app.py"}, "main", "code_projects/miapp")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_returns_failure_when_no_path(self):
        result = await _verify_file_written({}, "main", None)
        assert result["success"] is False
        assert "ruta" in result["message"].lower()


class TestExecutePlanActions:
    @pytest.mark.asyncio
    async def test_executes_multiple_actions(self):
        mock_add = AsyncMock()
        with (
            patch(
                "orchestration.executor.plan.safe_tool_call",
                new_callable=AsyncMock,
            ) as mock_tool,
            patch(
                "orchestration.executor.plan._verify_file_written",
                new_callable=AsyncMock,
            ) as mock_verify,
            patch(
                "core.path_resolver.paths.memory_dir",
                return_value=MagicMock(),
            ),
        ):
            mock_tool.return_value = {"output": "ok"}
            mock_verify.return_value = {"success": True}

            actions = [
                {"tool": "file_manager", "action": "write", "params": {"path": "app.py"}},
                {"tool": "git_manager", "action": "commit", "params": {}},
            ]
            report, wrote, committed, intended = await _execute_plan_actions(
                actions, "code_projects/miapp", "main", mock_add
            )
            assert len(report) == 2
            assert wrote is True
            assert "app.py" in intended

    @pytest.mark.asyncio
    async def test_normalizes_file_path_param(self):
        mock_add = AsyncMock()
        with (
            patch(
                "orchestration.executor.plan.safe_tool_call",
                new_callable=AsyncMock,
            ) as mock_tool,
            patch(
                "orchestration.executor.plan._verify_file_written",
                new_callable=AsyncMock,
            ) as mock_verify,
            patch(
                "core.path_resolver.paths.memory_dir",
                return_value=MagicMock(),
            ),
        ):
            mock_tool.return_value = {"output": "ok"}
            mock_verify.return_value = {"success": True}

            actions = [
                {"tool": "file_manager", "action": "write", "params": {"file_path": "app.py"}},
            ]
            _, _, _, intended = await _execute_plan_actions(
                actions, "code_projects/miapp", "main", mock_add
            )
            assert "app.py" in intended
