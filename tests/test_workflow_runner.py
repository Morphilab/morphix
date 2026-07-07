# tests/test_workflow_runner.py
"""Tests for WorkflowRunner, WorkflowResult, and cancellation."""

import asyncio
from unittest.mock import MagicMock

import pytest

from orchestration.context import Session
from orchestration.result_types import (
    failure,
    success,
    timeout,
)
from orchestration.runner import WorkflowCancelledError, WorkflowRunner


class TestWorkflowResult:
    def test_success_factory(self):
        r = success("done", subtasks=3)
        assert r.success is True
        assert r.content == "done"
        assert r.error is None
        assert r.timeout is False
        assert r.metadata["subtasks"] == 3

    def test_failure_factory(self):
        r = failure("boom", partial_content="half done", phase="decompose")
        assert r.success is False
        assert r.error == "boom"
        assert r.content == "half done"
        assert r.metadata["phase"] == "decompose"

    def test_timeout_factory(self):
        r = timeout(partial_content="partial", timeout_seconds=30)
        assert r.success is False
        assert r.timeout is True
        assert "30s" in r.error
        assert r.metadata["timeout_seconds"] == 30

    def test_timeout_defaults(self):
        r = timeout()
        assert r.content == ""
        assert r.metadata["timeout_seconds"] == 0


@pytest.fixture
def runner():
    session = Session(events=MagicMock(), context=MagicMock())
    session.context.cancelled = False
    return WorkflowRunner(session)


class TestWorkflowRunner:
    @pytest.mark.asyncio
    async def test_with_timeout_completes(self, runner):
        async def fast():
            return "done"

        result = await runner.with_timeout(fast(), 5, phase="test")
        assert result.success is True
        assert result.content == "done"
        assert result.metadata["phase"] == "test"

    @pytest.mark.asyncio
    async def test_with_timeout_triggers(self, runner):
        async def slow():
            await asyncio.sleep(10)
            return "too late"

        result = await runner.with_timeout(slow(), 0.1, phase="slow", fallback="timed out")
        assert result.success is False
        assert result.timeout is True
        assert "0.1s" in result.error
        assert result.content == "timed out"

    @pytest.mark.asyncio
    async def test_with_timeout_handles_exception(self, runner):
        async def buggy():
            raise ValueError("boom")

        result = await runner.with_timeout(buggy(), 5, phase="buggy")
        assert result.success is False
        assert result.error == "boom"
        assert result.timeout is False

    @pytest.mark.asyncio
    async def test_with_timeout_accepts_workflow_result(self, runner):
        async def already_typed():
            return success("pre-made")

        result = await runner.with_timeout(already_typed(), 5)
        assert result.success is True
        assert result.content == "pre-made"

    @pytest.mark.asyncio
    async def test_with_timeout_passes_through_none(self, runner):
        async def none_result():
            return None

        result = await runner.with_timeout(none_result(), 5)
        assert result.success is True
        assert result.content == ""

    @pytest.mark.asyncio
    async def test_cancellation_raises(self, runner):
        runner.session.cancel()

        async def work():
            return "done"

        with pytest.raises(WorkflowCancelledError):
            await runner.with_timeout(work(), 5)

    @pytest.mark.asyncio
    async def test_safe_call_returns_fallback_on_error(self, runner):
        async def crash():
            raise RuntimeError("gone")

        result = await runner.safe_call(crash(), fallback="default")
        assert result == "default"

    @pytest.mark.asyncio
    async def test_safe_call_returns_value_on_success(self, runner):
        async def work():
            return 42

        result = await runner.safe_call(work(), fallback=0)
        assert result == 42

    @pytest.mark.asyncio
    async def test_phase_stats_tracks_elapsed(self, runner):
        async def quick():
            return "ok"

        await runner.with_timeout(quick(), 5, phase="alpha")
        await runner.with_timeout(quick(), 5, phase="beta")

        stats = runner.phase_stats()
        assert "alpha" in stats
        assert "beta" in stats
        assert runner.elapsed() > 0
