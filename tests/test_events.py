# tests/test_events.py
from unittest.mock import AsyncMock

import pytest

from orchestration.events import (
    WorkflowContext,
    WorkflowEvents,
    emit_agent,
    emit_assistant,
    emit_diagram,
    emit_refresh,
    emit_stats,
    emit_system,
    emit_user,
)


class TestWorkflowContext:
    def test_minimal_context(self):
        ctx = WorkflowContext(query="test query")
        assert ctx.query == "test query"
        assert ctx.workspace == "main"
        assert ctx.conversation_history == []

    def test_full_context(self):
        ctx = WorkflowContext(
            query="task",
            workspace="dev",
            project_root="code_projects/miapp",
            active_workflow="tdd",
            allowed_tools=["file_manager"],
            enc="mock_encoding",
        )
        assert ctx.project_root == "code_projects/miapp"
        assert ctx.active_workflow == "tdd"
        assert ctx.allowed_tools == ["file_manager"]


class TestWorkflowEvents:
    def test_events_with_no_callbacks(self):
        events = WorkflowEvents()
        assert events.on_system_message is None
        assert events.on_assistant_message is None
        assert events.on_diagram_update is None

    def test_events_with_callbacks(self):
        cb = AsyncMock()
        events = WorkflowEvents(on_system_message=cb, on_stats_update=cb)
        assert events.on_system_message is cb
        assert events.on_assistant_message is None


class TestEmitHelpers:
    @pytest.mark.asyncio
    async def test_emit_system_calls_callback(self):
        cb = AsyncMock()
        events = WorkflowEvents(on_system_message=cb)
        await emit_system(events, "hola")
        cb.assert_awaited_once_with("hola")

    @pytest.mark.asyncio
    async def test_emit_system_no_callback(self):
        events = WorkflowEvents()
        await emit_system(events, "hola")  # No debería lanzar error

    @pytest.mark.asyncio
    async def test_emit_assistant(self):
        cb = AsyncMock()
        events = WorkflowEvents(on_assistant_message=cb)
        await emit_assistant(events, "respuesta")
        cb.assert_awaited_once_with("respuesta")

    @pytest.mark.asyncio
    async def test_emit_user(self):
        cb = AsyncMock()
        events = WorkflowEvents(on_user_message=cb)
        await emit_user(events, "user msg")
        cb.assert_awaited_once_with("user msg")

    @pytest.mark.asyncio
    async def test_emit_agent(self):
        cb = AsyncMock()
        events = WorkflowEvents(on_agent_message=cb)
        await emit_agent(events, "analista", "Ronda 1", "respuesta del agente")
        cb.assert_awaited_once_with("analista", "Ronda 1", "respuesta del agente")

    @pytest.mark.asyncio
    async def test_emit_diagram(self):
        cb = AsyncMock()
        events = WorkflowEvents(on_diagram_update=cb)
        graph = {"nodes": 3}
        await emit_diagram(events, "graph TD; A-->B", graph)
        cb.assert_awaited_once_with("graph TD; A-->B", graph)

    @pytest.mark.asyncio
    async def test_emit_stats(self):
        cb = AsyncMock()
        events = WorkflowEvents(on_stats_update=cb)
        await emit_stats(events, {"cpu": 80})
        cb.assert_awaited_once_with({"cpu": 80})

    @pytest.mark.asyncio
    async def test_emit_refresh(self):
        cb = AsyncMock()
        events = WorkflowEvents(on_ui_refresh=cb)
        await emit_refresh(events)
        cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_callback_exception_is_suppressed(self):
        cb = AsyncMock(side_effect=RuntimeError("UI rota"))
        events = WorkflowEvents(on_system_message=cb)
        # No debe lanzar
        await emit_system(events, "test")
        cb.assert_awaited_once()
