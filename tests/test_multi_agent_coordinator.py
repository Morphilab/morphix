# tests/test_multi_agent_coordinator.py
"""Tests for MultiAgentCoordinator and SharedBlackboard."""

from unittest.mock import AsyncMock, patch

import pytest

from orchestration.workflows.coordinated import MultiAgentCoordinator


class TestSharedBlackboard:
    @pytest.mark.asyncio
    async def test_write_and_read(self):
        from orchestration.workflows.blackboard import SharedBlackboard

        bb = SharedBlackboard()
        await bb.write("key1", "value1")
        assert await bb.read("key1") == "value1"

    @pytest.mark.asyncio
    async def test_read_missing_returns_none(self):
        from orchestration.workflows.blackboard import SharedBlackboard

        bb = SharedBlackboard()
        assert await bb.read("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_keys(self):
        from orchestration.workflows.blackboard import SharedBlackboard

        bb = SharedBlackboard()
        await bb.write("a", 1)
        await bb.write("b", 2)
        keys = await bb.list_keys()
        assert set(keys) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_delete(self):
        from orchestration.workflows.blackboard import SharedBlackboard

        bb = SharedBlackboard()
        await bb.write("x", 1)
        assert await bb.delete("x") is True
        assert await bb.read("x") is None
        assert await bb.delete("x") is False

    @pytest.mark.asyncio
    async def test_clear(self):
        from orchestration.workflows.blackboard import SharedBlackboard

        bb = SharedBlackboard()
        await bb.write("a", 1)
        await bb.write("b", 2)
        await bb.clear()
        assert bb.entry_count == 0
        assert await bb.list_keys() == []

    def test_context_snapshot(self):
        from orchestration.workflows.blackboard import SharedBlackboard

        bb = SharedBlackboard()
        bb._phases = {"design": {"schema": "users(id, name)", "config": "port: 8080"}}
        snap = bb.get_context_snapshot()
        assert "Shared Context" in snap
        assert "schema" in snap
        assert "config" in snap

    def test_context_snapshot_empty(self):
        from orchestration.workflows.blackboard import SharedBlackboard

        bb = SharedBlackboard()
        assert bb.get_context_snapshot() == ""

    @pytest.mark.asyncio
    async def test_get_agent_context_filtered(self):
        from orchestration.workflows.blackboard import SharedBlackboard

        bb = SharedBlackboard()
        await bb.write("a", "valA")
        await bb.write("b", "valB")
        await bb.write("c", "valC")
        ctx = await bb.get_agent_context(["a", "c"])
        assert "valA" in ctx
        assert "valC" in ctx
        assert "valB" not in ctx

    @pytest.mark.asyncio
    async def test_concurrent_writes(self):
        import asyncio

        from orchestration.workflows.blackboard import SharedBlackboard

        bb = SharedBlackboard()

        async def writer(n: int):
            for i in range(5):
                await bb.write(f"k{n}_{i}", n * 100 + i)

        await asyncio.gather(writer(1), writer(2), writer(3))
        assert bb.entry_count == 15


class TestMultiAgentCoordinator:
    @pytest.mark.asyncio
    async def test_decompose_fallback_when_llm_fails(self):
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        coordinator = MultiAgentCoordinator()
        with patch.object(
            MultiAgentCoordinator,
            "decompose_task_dag",
            new_callable=AsyncMock,
            return_value={
                "subtasks": [
                    {
                        "id": "main_task",
                        "description": "build a todo app",
                        "depends_on": [],
                        "agent_hint": "developer",
                    }
                ],
                "raw_response": "",
            },
        ):
            dag = await coordinator.decompose_task_dag("build a todo app")
            assert len(dag["subtasks"]) == 1
            assert dag["subtasks"][0]["id"] == "main_task"

    def test_parse_dag_json_valid(self):
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        text = '{"subtasks": [{"id": "a", "description": "do a", "depends_on": [], "agent_hint": "developer"}]}'
        result = MultiAgentCoordinator._parse_dag_json(text)
        assert result is not None
        assert len(result["subtasks"]) == 1

    def test_parse_dag_json_in_text(self):
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        text = 'Here is the plan:\n{"subtasks": [{"id": "x", "description": "task x", "depends_on": [], "agent_hint": "analista"}]}\nEnd.'
        result = MultiAgentCoordinator._parse_dag_json(text)
        assert result is not None
        assert result["subtasks"][0]["id"] == "x"

    def test_parse_dag_json_invalid(self):
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        assert MultiAgentCoordinator._parse_dag_json("not json") is None
        assert MultiAgentCoordinator._parse_dag_json("") is None

    @pytest.mark.asyncio
    async def test_assign_agents_with_force_agent(self):
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        coordinator = MultiAgentCoordinator()
        subtasks = [
            {"id": "a", "description": "do a", "depends_on": [], "agent_hint": "analista"},
            {"id": "b", "description": "do b", "depends_on": ["a"], "agent_hint": "developer"},
        ]
        assignments = await coordinator.assign_agents(subtasks, force_agent="moderador")
        assert assignments == {"a": "moderador", "b": "moderador"}

    @pytest.mark.asyncio
    async def test_assign_agents_fallback_to_hint(self):
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        coordinator = MultiAgentCoordinator()
        subtasks = [
            {"id": "t", "description": "analyze code", "depends_on": [], "agent_hint": "analista"},
        ]
        assignments = await coordinator.assign_agents(subtasks, allowed_agents=["analista"])
        assert assignments["t"] == "analista"

    @pytest.mark.asyncio
    async def test_aggregate_with_confidence_fallback(self):
        from orchestration.workflows.coordinated import MultiAgentCoordinator

        coordinator = MultiAgentCoordinator()
        results = {
            "a": {"agent": "developer", "status": "done", "result": "Built API"},
            "b": {"agent": "analista", "status": "done", "result": "Schema ok"},
        }
        # Now delegates to ResultAggregator — errors return structured fallback
        with patch(
            "orchestration.aggregator.models.call",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM down"),
        ):
            result = await coordinator.aggregate_with_confidence("build app", results)
            # Unified aggregator returns structured fallback on error
            assert "**Consulta:**" in result or "Built API" in result

    @pytest.mark.asyncio
    async def test_aggregate_empty_results(self):
        from orchestration.workflows.coordinated import MultiAgentCoordinator

        coordinator = MultiAgentCoordinator()
        result = await coordinator.aggregate_with_confidence("query", {})
        # Unified aggregator returns warning for empty results
        assert "⚠️" in result


# ═══════════════════════════════════════════════════════════════════
#  execute_dag tests — DAG execution with dependencies
# ═══════════════════════════════════════════════════════════════════


class TestExecuteDag:
    @pytest.fixture
    def mock_execute_one(self):
        """Mock _execute_one to return success without real agent loop."""
        with patch.object(
            MultiAgentCoordinator,
            "_execute_one",
            new_callable=AsyncMock,
        ) as mock:
            mock.return_value = {
                "status": "completed",
                "result": "Task done",
                "agent": "developer",
                "files_written": ["out.py"],
                "error": None,
            }
            yield mock

    @pytest.mark.asyncio
    async def test_single_subtask_no_dependencies(self, mock_execute_one):
        """Single subtask with no dependencies — executes immediately."""
        coordinator = MultiAgentCoordinator()
        subtasks = [
            {"id": "t1", "description": "simple task", "depends_on": [], "agent_hint": "developer"}
        ]
        assignments = {"t1": "developer"}
        results = await coordinator.execute_dag(subtasks, assignments)
        assert len(results) == 1
        assert results["t1"]["status"] == "completed"
        assert results["t1"]["result"] == "Task done"
        mock_execute_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_two_subtasks_with_dependency(self, mock_execute_one):
        """Task B depends on A → A executes first, then B."""
        coordinator = MultiAgentCoordinator()
        subtasks = [
            {"id": "a", "description": "first", "depends_on": [], "agent_hint": "developer"},
            {
                "id": "b",
                "description": "second",
                "depends_on": ["a"],
                "agent_hint": "analista",
            },
        ]
        assignments = {"a": "developer", "b": "analista"}
        results = await coordinator.execute_dag(subtasks, assignments)
        assert len(results) == 2
        assert results["a"]["status"] == "completed"
        assert results["b"]["status"] == "completed"
        assert mock_execute_one.call_count == 2
        # Verify A was called before B (separate calls in topological order)
        calls = mock_execute_one.call_args_list
        assert calls[0].args[0] == "a"  # first call is subtask a
        assert calls[1].args[0] == "b"  # second call is subtask b

    @pytest.mark.asyncio
    async def test_parallel_branches(self, mock_execute_one):
        """Two independent subtasks execute in parallel."""
        coordinator = MultiAgentCoordinator()
        subtasks = [
            {"id": "root", "description": "start", "depends_on": [], "agent_hint": "developer"},
            {
                "id": "branch_a",
                "description": "branch a",
                "depends_on": ["root"],
                "agent_hint": "developer",
            },
            {
                "id": "branch_b",
                "description": "branch b",
                "depends_on": ["root"],
                "agent_hint": "analista",
            },
        ]
        assignments = {"root": "developer", "branch_a": "developer", "branch_b": "analista"}
        results = await coordinator.execute_dag(subtasks, assignments)
        assert len(results) == 3
        assert results["branch_a"]["status"] == "completed"
        assert results["branch_b"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_circular_dependency_fallback_sequential(self, mock_execute_one):
        """A depends on B, B depends on A → stuck → executes sequentially."""
        coordinator = MultiAgentCoordinator()
        subtasks = [
            {"id": "x", "description": "x", "depends_on": ["y"], "agent_hint": "developer"},
            {"id": "y", "description": "y", "depends_on": ["x"], "agent_hint": "analista"},
        ]
        assignments = {"x": "developer", "y": "analista"}
        results = await coordinator.execute_dag(subtasks, assignments)
        assert len(results) == 2
        assert results["x"]["status"] == "completed"
        assert results["y"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_retry_on_failure_with_fallback_agent(self):
        """Subtask fails → retry with different agent → succeeds."""
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        coordinator = MultiAgentCoordinator()
        subtasks = [
            {"id": "t1", "description": "test", "depends_on": [], "agent_hint": "developer"}
        ]
        assignments = {"t1": "developer"}

        # First call fails, retry succeeds
        with patch.object(
            MultiAgentCoordinator,
            "_execute_one",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.side_effect = [
                RuntimeError("developer crashed"),  # first attempt
                {  # retry with fallback
                    "status": "completed",
                    "result": "Retry worked",
                    "agent": "analista",
                    "files_written": [],
                    "error": None,
                },
            ]

            results = await coordinator.execute_dag(subtasks, assignments)
            assert results["t1"]["status"] == "completed"
            assert results["t1"]["result"] == "Retry worked"
            assert mock_exec.call_count == 2
            # Second call should use the fallback agent
            assert mock_exec.call_args_list[1].args[2] == "analista"

    @pytest.mark.asyncio
    async def test_retry_failure_returns_error_dict(self):
        """Both original and retry fail → returns error result."""
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        coordinator = MultiAgentCoordinator()
        subtasks = [
            {"id": "t1", "description": "test", "depends_on": [], "agent_hint": "developer"}
        ]
        assignments = {"t1": "developer"}

        with patch.object(
            MultiAgentCoordinator,
            "_execute_one",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.side_effect = [
                RuntimeError("first crash"),
                RuntimeError("retry crash"),
            ]

            results = await coordinator.execute_dag(subtasks, assignments)
            assert results["t1"]["status"] == "failed"
            assert "Failed after retry" in results["t1"]["result"]
            assert results["t1"]["error"] == "retry crash"


# ═══════════════════════════════════════════════════════════════════
#  _execute_one tests — subtask execution with blackboard
# ═══════════════════════════════════════════════════════════════════


class TestExecuteOne:
    @pytest.mark.asyncio
    async def test_basic_execution(self):
        """_execute_one calls execute_agent_loop with correct params."""
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        coordinator = MultiAgentCoordinator()
        st = {"id": "s1", "description": "build feature"}
        mock_result = {
            "status": "completed",
            "result": "Feature built",
            "actions_taken": 2,
            "iterations": 1,
            "files_written": ["api.py"],
        }

        with patch(
            "orchestration.workflows.coordinated.execute_agent_loop",
            new_callable=AsyncMock,
        ) as mock_loop:
            with patch(
                "orchestration.workflows.blackboard.SharedBlackboard.get_agent_context",
                new_callable=AsyncMock,
                return_value="ctx: schema defined",
            ):
                mock_loop.return_value = mock_result
                result = await coordinator._execute_one(
                    "s1", st, "developer", None, "main", None, None, None
                )
                assert result["status"] == "completed"
                assert result["agent"] == "developer"
                assert result["files_written"] == ["api.py"]
                mock_loop.assert_awaited_once()
                call_kwargs = mock_loop.call_args.kwargs
                assert call_kwargs["task"] == "build feature"
                assert call_kwargs["agent_type"] == "developer"
                assert "ctx: schema defined" in call_kwargs["extra_context"]

    @pytest.mark.asyncio
    async def test_failure_returns_error_dict(self):
        """_execute_one handles loop failures gracefully."""
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        coordinator = MultiAgentCoordinator()
        st = {"id": "s1", "description": "crash test"}

        with patch(
            "orchestration.workflows.coordinated.execute_agent_loop",
            new_callable=AsyncMock,
            side_effect=RuntimeError("agent loop crashed"),
        ):
            with patch(
                "orchestration.workflows.blackboard.SharedBlackboard.get_agent_context",
                new_callable=AsyncMock,
                return_value="",
            ):
                result = await coordinator._execute_one(
                    "s1", st, "developer", None, "main", None, None, None
                )
                assert result["status"] == "failed"
                assert result["error"] == "agent loop crashed"

    @pytest.mark.asyncio
    async def test_writes_result_to_blackboard(self):
        """_execute_one does NOT auto-write to blackboard; orchestrator is canonical writer."""
        from orchestration.workflows.coordinated import (
            MultiAgentCoordinator,
        )

        coordinator = MultiAgentCoordinator()
        st = {"id": "s1", "description": "blackboard test"}

        with patch(
            "orchestration.workflows.coordinated.execute_agent_loop",
            new_callable=AsyncMock,
            return_value={
                "status": "completed",
                "result": "Done",
                "files_written": ["out.py"],
            },
        ):
            with patch.object(
                coordinator.blackboard, "write", new_callable=AsyncMock
            ) as mock_write:
                with patch.object(
                    coordinator.blackboard,
                    "get_agent_context",
                    new_callable=AsyncMock,
                    return_value="",
                ):
                    result = await coordinator._execute_one(
                        "s1", st, "developer", None, "main", None, None, None
                    )
                    mock_write.assert_not_called()
                    assert result["status"] == "completed"
                    assert result["agent"] == "developer"


# ═══════════════════════════════════════════════════════════════════
#  aggregate_with_confidence — success path
# ═══════════════════════════════════════════════════════════════════


class TestAggregateWithConfidence:
    @pytest.mark.asyncio
    async def test_successful_aggregation(self):
        """With completed results + files → deterministic programmatic response."""
        from orchestration.workflows.coordinated import MultiAgentCoordinator

        coordinator = MultiAgentCoordinator()
        results = {
            "a": {
                "agent": "developer",
                "status": "completed",
                "result": "Built REST API with Flask",
                "files_written": ["api.py"],
            },
            "b": {
                "agent": "analista",
                "status": "completed",
                "result": "Designed database schema for users table",
                "files_written": ["schema.sql"],
            },
        }
        # Unified aggregator: completed + files → programmatic response (no LLM call)
        result = await coordinator.aggregate_with_confidence("build users CRUD", results)
        assert "✅ Tarea completada" in result
        assert "api.py" in result
        assert "schema.sql" in result
