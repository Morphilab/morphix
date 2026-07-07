"""Tests for TDD loop — automated test-driven development cycle."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_tdd_deps():
    """Mock safe_tool_call (test runner) and execute_agent_loop for TDD cycle."""
    with (
        patch("orchestration.workflows.tdd.safe_tool_call", new_callable=AsyncMock) as mock_tool,
        patch("orchestration.loop.execute_agent_loop", new_callable=AsyncMock) as mock_loop,
    ):
        yield mock_tool, mock_loop


class TestTddLoop:
    @pytest.mark.asyncio
    async def test_tests_pass_immediately(self, mock_tdd_deps):
        """Tests pass on first run → completed immediately."""
        mock_tool, mock_loop = mock_tdd_deps
        mock_tool.return_value = {
            "success": True,
            "output": {
                "output": "All tests passed.",
                "success": True,
                "failed_count": 0,
                "error_count": 0,
            },
        }

        from orchestration.workflows.tdd import execute_tdd_loop

        result = await execute_tdd_loop(task="Write a function")
        assert result["status"] == "completed"
        assert result["iterations"] == 1
        assert "tests pasan" in result["result"]
        mock_loop.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_fixes_then_tests_pass(self, mock_tdd_deps):
        """Tests fail → agent fixes → tests pass on second run."""
        mock_tool, mock_loop = mock_tdd_deps

        # First test run: fails
        # Second test run: passes
        mock_tool.side_effect = [
            {
                "success": False,
                "output": {
                    "output": "1 test failed: assert 2 == 3",
                    "success": False,
                    "failed_count": 1,
                    "error_count": 0,
                },
            },
            {
                "success": True,
                "output": {
                    "output": "All tests passed.",
                    "success": True,
                    "failed_count": 0,
                    "error_count": 0,
                },
            },
        ]
        mock_loop.return_value = {
            "status": "completed",
            "result": "Fixed the assertion in test file",
            "actions_taken": 2,
            "iterations": 1,
            "files_written": ["test_app.py"],
        }

        from orchestration.workflows.tdd import execute_tdd_loop

        result = await execute_tdd_loop(
            task="Fix failing test",
            agent_type="developer",
            workspace="main",
        )
        assert result["status"] == "completed"
        assert result["iterations"] == 2
        assert "test_app.py" in result["files_modified"]
        mock_loop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_stalled_returns_failed(self, mock_tdd_deps):
        """Agent gets stuck → returns failed immediately."""
        mock_tool, mock_loop = mock_tdd_deps
        mock_tool.return_value = {
            "success": False,
            "output": {
                "output": "3 tests failed, 2 errors",
                "success": False,
                "failed_count": 3,
                "error_count": 2,
            },
        }
        mock_loop.return_value = {
            "status": "stalled",
            "result": "Agent stuck, can't figure out the issue",
            "actions_taken": 5,
            "iterations": 3,
            "files_written": [],
        }

        from orchestration.workflows.tdd import execute_tdd_loop

        result = await execute_tdd_loop(task="Fix broken code", max_iterations=3)
        assert result["status"] == "failed"
        assert "estancado" in result["result"]
        assert result["iterations"] == 1

    @pytest.mark.asyncio
    async def test_max_iterations_reached(self, mock_tdd_deps):
        """Tests never pass, agent keeps trying → fails after max iterations."""
        mock_tool, mock_loop = mock_tdd_deps

        # All test runs return failures
        mock_tool.return_value = {
            "success": False,
            "output": {
                "output": "Still failing after fix",
                "success": False,
                "failed_count": 1,
                "error_count": 0,
            },
        }
        mock_loop.return_value = {
            "status": "completed",
            "result": "Tried to fix",
            "actions_taken": 1,
            "iterations": 1,
            "files_written": [],
        }

        from orchestration.workflows.tdd import execute_tdd_loop

        result = await execute_tdd_loop(task="Never passing test", max_iterations=2)
        assert result["status"] == "failed"
        assert result["iterations"] == 2
        assert "Límite de 2 iteraciones" in result["result"]
        assert mock_loop.call_count == 2

    @pytest.mark.asyncio
    async def test_custom_max_iterations(self, mock_tdd_deps):
        """max_iterations parameter overrides default of 5."""
        mock_tool, mock_loop = mock_tdd_deps
        mock_tool.return_value = {
            "success": False,
            "output": {
                "output": "fail",
                "success": False,
                "failed_count": 1,
                "error_count": 0,
            },
        }
        mock_loop.return_value = {
            "status": "completed",
            "result": "fix attempt",
            "actions_taken": 1,
            "iterations": 1,
            "files_written": [],
        }

        from orchestration.workflows.tdd import execute_tdd_loop

        result = await execute_tdd_loop(task="test", max_iterations=1)
        assert result["iterations"] == 1
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_accumulates_files_across_iterations(self, mock_tdd_deps):
        """Files modified by agent in different iterations are accumulated."""
        mock_tool, mock_loop = mock_tdd_deps

        mock_tool.side_effect = [
            {
                "success": False,
                "output": {
                    "output": "fail",
                    "success": False,
                    "failed_count": 1,
                    "error_count": 0,
                },
            },
            {
                "success": True,
                "output": {
                    "output": "pass",
                    "success": True,
                    "failed_count": 0,
                    "error_count": 0,
                },
            },
        ]
        mock_loop.return_value = {
            "status": "completed",
            "result": "Fixed file",
            "actions_taken": 2,
            "iterations": 1,
            "files_written": ["src/module.py", "tests/test_module.py"],
        }

        from orchestration.workflows.tdd import execute_tdd_loop

        result = await execute_tdd_loop(task="Add feature")
        assert result["status"] == "completed"
        assert "src/module.py" in result["files_modified"]
        assert "tests/test_module.py" in result["files_modified"]
        assert len(result["files_modified"]) == 2

    @pytest.mark.asyncio
    async def test_green_field_prompts_agent_to_write_tests(self, mock_tdd_deps):
        """Sin tests (green-field) → el agente recibe el prompt de ESCRIBIR tests, no de corregir."""
        mock_tool, mock_loop = mock_tdd_deps
        mock_tool.side_effect = [
            {  # iter 1: no hay tests
                "success": False,
                "output": {
                    "output": "no tests ran in 0.01s",
                    "success": False,
                    "failed_count": 0,
                    "error_count": 0,
                },
            },
            {  # iter 2: pasan
                "success": True,
                "output": {
                    "output": "1 passed",
                    "success": True,
                    "failed_count": 0,
                    "error_count": 0,
                },
            },
        ]
        mock_loop.return_value = {
            "status": "completed",
            "result": "Tests + implementación escritos",
            "files_written": ["test_es_primo.py", "es_primo.py"],
        }

        from orchestration.workflows.tdd import execute_tdd_loop

        with patch("orchestration.workflows.tdd._project_has_no_tests", return_value=True):
            result = await execute_tdd_loop(
                task="Implementa es_primo con TDD", project_root="code_projects/lab"
            )
        assert result["status"] == "completed"
        # El primer prompt al agente debe ser el green-field (escribir tests)
        first_task = mock_loop.call_args_list[0].kwargs["task"]
        assert "PRIMERO escribe los tests" in first_task

    @pytest.mark.asyncio
    async def test_project_root_propagates_to_test_runner(self, mock_tdd_deps):
        """test_runner recibe el project_root y file_path='.' (descubrimiento en el proyecto)."""
        mock_tool, mock_loop = mock_tdd_deps
        mock_tool.return_value = {
            "success": True,
            "output": {"output": "1 passed", "success": True, "failed_count": 0, "error_count": 0},
        }

        from orchestration.workflows.tdd import execute_tdd_loop

        await execute_tdd_loop(task="x", project_root="code_projects/lab")
        params = mock_tool.call_args.kwargs["parameters"]
        assert params["project_root"] == "code_projects/lab"
        assert params["file_path"] == "."


def test_project_has_no_tests_filesystem(tmp_path):
    """_project_has_no_tests detecta green-field escaneando el directorio del proyecto."""
    from orchestration.workflows.tdd import _project_has_no_tests

    with patch("orchestration.workflows.tdd.paths.code_projects_dir", return_value=tmp_path):
        assert _project_has_no_tests("main", "code_projects/lab", []) is True
        (tmp_path / "test_es_primo.py").write_text("def test_x():\n    assert True\n")
        assert _project_has_no_tests("main", "code_projects/lab", []) is False
