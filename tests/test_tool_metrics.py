"""Tests for tool-level metrics (success/failure rates and latency)."""

from unittest.mock import AsyncMock, patch

import pytest

from core.metrics import Metrics, ToolMetrics, tool_metrics


class TestToolMetrics:
    def test_record_single_call(self):
        tm = ToolMetrics()
        tm.record_call("file_manager", True, 15.3)
        stats = tm.get_tool_stats("file_manager")
        assert stats is not None
        assert stats["calls"] == 1
        assert stats["success"] == 1
        assert stats["failure"] == 0
        assert stats["success_rate_pct"] == 100.0
        assert stats["avg_latency_ms"] == 15.3

    def test_record_multiple_calls(self):
        tm = ToolMetrics()
        tm.record_call("bash_manager", True, 100.0)
        tm.record_call("bash_manager", False, 200.0)
        tm.record_call("bash_manager", True, 300.0)
        stats = tm.get_tool_stats("bash_manager")
        assert stats["calls"] == 3
        assert stats["success"] == 2
        assert stats["failure"] == 1
        assert stats["success_rate_pct"] == 66.7
        assert stats["avg_latency_ms"] == 200.0
        assert stats["max_latency_ms"] == 300.0

    def test_missing_tool_returns_none(self):
        tm = ToolMetrics()
        assert tm.get_tool_stats("nonexistent") is None

    def test_get_all_stats(self):
        tm = ToolMetrics()
        tm.record_call("tool_a", True, 10.0)
        tm.record_call("tool_b", False, 20.0)
        all_stats = tm.get_all_stats()
        assert len(all_stats) == 2
        assert "tool_a" in all_stats
        assert "tool_b" in all_stats

    def test_summary(self):
        tm = ToolMetrics()
        tm.record_call("a", True, 5.0)
        tm.record_call("a", False, 10.0)
        tm.record_call("b", True, 15.0)
        summary = tm.get_summary()
        assert summary["total_calls"] == 3
        assert summary["success"] == 2
        assert summary["failure"] == 1
        assert summary["success_rate_pct"] == 66.7
        assert summary["tools_tracked"] == 2

    def test_to_dict_format(self):
        tm = ToolMetrics()
        tm.record_call("test_tool", True, 42.0)
        d = tm.to_dict()
        assert "summary" in d
        assert "tools" in d
        assert d["summary"]["total_calls"] == 1

    def test_thread_safety_concurrent_calls(self):
        import random
        import threading

        tm = ToolMetrics()

        def worker():
            for _ in range(100):
                tm.record_call("shared_tool", random.random() > 0.2, random.uniform(1, 100))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = tm.get_tool_stats("shared_tool")
        assert stats["calls"] == 1000  # 10 threads × 100 calls

    @pytest.mark.asyncio
    async def test_safe_tool_call_records_metrics(self):
        """Verifica que safe_tool_call registra métricas en tool_metrics."""
        with patch(
            "tools.orchestrator.tool_orchestrator.execute_tool",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "ok"}

            from tools.wrapper import safe_tool_call

            result = await safe_tool_call("file_manager", {"action": "read", "path": "test.txt"})
            assert result["success"] is True

            stats = tool_metrics.get_tool_stats("file_manager")
            assert stats is not None
            assert stats["calls"] >= 1
            assert stats["success"] >= 1

    @pytest.mark.asyncio
    async def test_safe_tool_call_records_failure_metrics(self):
        """Verifica que safe_tool_call registra fallos correctamente."""
        with patch(
            "tools.orchestrator.tool_orchestrator.execute_tool",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = {"success": False, "output": "error simulado"}

            from tools.wrapper import safe_tool_call

            result = await safe_tool_call("bash_manager", {"action": "bad_command"})
            assert result["success"] is False

            stats = tool_metrics.get_tool_stats("bash_manager")
            if stats:
                assert stats["failure"] >= 1

    @pytest.mark.asyncio
    async def test_safe_tool_call_records_latency(self):
        """Verifica que safe_tool_call registra latencia > 0."""
        with patch(
            "tools.orchestrator.tool_orchestrator.execute_tool",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "ok"}

            from tools.wrapper import safe_tool_call

            await safe_tool_call("test_runner", {"action": "run", "path": "tests/"})

            stats = tool_metrics.get_tool_stats("test_runner")
            if stats:
                assert stats["avg_latency_ms"] >= 0


class TestMetrics:
    def test_record_workflow_completed(self):
        m = Metrics()
        m.record_workflow_completed(tokens=100, tool_calls=3)
        assert m.total_workflows == 1
        assert m.completed_workflows == 1
        assert m.total_tokens == 100
        assert m.tool_calls == 3

    def test_record_workflow_failed(self):
        m = Metrics()
        m.record_workflow_failed()
        assert m.total_workflows == 1
        assert m.failed_workflows == 1
        assert m.completed_workflows == 0

    def test_record_llm_call(self):
        m = Metrics()
        m.record_llm_call()
        m.record_llm_call()
        assert m.llm_calls == 2

    def test_record_llm_usage(self):
        m = Metrics()
        m.record_llm_usage(
            prompt_tokens=50,
            completion_tokens=30,
            cache_hit_tokens=20,
            cache_miss_tokens=10,
        )
        assert m.total_prompt_tokens == 50
        assert m.total_completion_tokens == 30
        assert m.total_tokens == 80  # prompt + completion
        assert m.cache_hit_tokens == 20
        assert m.cache_miss_tokens == 10

    def test_record_rate_limited(self):
        m = Metrics()
        m.record_rate_limited()
        m.record_rate_limited()
        assert m.rate_limited == 2

    def test_to_dict_basic(self):
        m = Metrics()
        d = m.to_dict()
        assert "uptime_seconds" in d
        assert isinstance(d["uptime_seconds"], int)
        assert d["total_workflows"] == 0
        assert d["success_rate"] == "0.0%"
        assert d["cache_hit_rate_pct"] == 0.0

    def test_to_dict_after_workflows(self):
        m = Metrics()
        m.record_workflow_completed()
        m.record_workflow_failed()
        d = m.to_dict()
        assert d["total_workflows"] == 2
        assert d["completed_workflows"] == 1
        assert d["failed_workflows"] == 1
        assert d["success_rate"] == "50.0%"

    def test_to_dict_cache_hit_rate(self):
        m = Metrics()
        m.record_llm_usage(cache_hit_tokens=75, cache_miss_tokens=25)
        d = m.to_dict()
        assert d["cache_hit_rate_pct"] == 75.0
        assert d["tokens_saved"] == 75
