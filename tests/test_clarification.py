"""Tests for clarification requests mechanism — pause and resume workflow."""

import pytest


class TestAskClarificationTool:
    def test_tool_is_registered(self):
        """La herramienta ask_clarification está registrada en tools_registry."""
        import tools.ask_clarification  # noqa: F401 — triggers registration
        from tools.registry import tools_registry

        assert (
            "ask_clarification" in tools_registry._tools
        ), "ask_clarification debe estar registrada en tools_registry"

    def test_tool_returns_question(self):
        """La herramienta retorna la pregunta y opciones."""
        import asyncio

        from tools.ask_clarification import _ask_clarification_tool

        result = asyncio.run(
            _ask_clarification_tool(
                question="¿Qué URL quieres scrapear?",
                options=["https://example.com", "https://otra.com"],
            )
        )
        assert result["success"] is True
        assert result["question"] == "¿Qué URL quieres scrapear?"
        assert "https://example.com" in result["options"]

    def test_tool_defaults_empty_options(self):
        """Sin options, la herramienta devuelve lista vacía."""
        import asyncio

        from tools.ask_clarification import _ask_clarification_tool

        result = asyncio.run(_ask_clarification_tool(question="¿Qué carpeta respaldar?"))
        assert result["options"] == []


class TestLoopInterception:
    @pytest.mark.asyncio
    async def test_clarification_tool_returns_pause_dict(self):
        """Cuando el agent loop recibe ask_clarification, retorna dict de pausa."""
        from orchestration.loop import AgentLoopConfig, _execute_tool_calls_and_check_stall

        tool_calls = [
            {
                "name": "ask_clarification",
                "id": "call_1",
                "arguments": {
                    "question": "¿Qué URL quieres?",
                    "options": ["url1", "url2"],
                },
            }
        ]
        result = await _execute_tool_calls_and_check_stall(
            tool_calls=tool_calls,
            messages=[],
            files_written=[],
            actions_taken=0,
            iteration_modified=False,
            consecutive_stalls=0,
            iteration=1,
            config=AgentLoopConfig(max_agent_iterations=5, max_stall_iterations=2),
            project_root=None,
            workspace="main",
            events=None,
        )

        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["status"] == "clarification_needed"
        assert result["clarification_question"] == "¿Qué URL quieres?"
        assert "url1" in result["clarification_options"]
        assert "paused_loop_state" in result
        assert result["paused_loop_state"]["iteration"] == 1

    @pytest.mark.asyncio
    async def test_normal_tool_returns_tuple(self):
        """Una herramienta normal sigue devolviendo tupla."""
        from orchestration.loop import AgentLoopConfig, _execute_tool_calls_and_check_stall

        tool_calls = [
            {
                "name": "file_manager",
                "id": "call_2",
                "arguments": {"action": "read", "path": "test.py"},
            }
        ]
        result = await _execute_tool_calls_and_check_stall(
            tool_calls=tool_calls,
            messages=[],
            files_written=[],
            actions_taken=0,
            iteration_modified=False,
            consecutive_stalls=0,
            iteration=1,
            config=AgentLoopConfig(max_agent_iterations=5, max_stall_iterations=2),
            project_root=None,
            workspace="main",
            events=None,
        )

        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
        assert len(result) == 5  # (actions_taken, modified, files, stalls, early)


class TestWorkflowContextClarification:
    def test_context_has_clarification_field(self):
        """WorkflowContext tiene el campo last_clarification."""
        from orchestration.context import WorkflowContext

        ctx = WorkflowContext(query="test", last_clarification="pregunta")
        assert ctx.last_clarification == "pregunta"

    def test_context_default_empty(self):
        """Por defecto, last_clarification es string vacío."""
        from orchestration.context import WorkflowContext

        ctx = WorkflowContext(query="test")
        assert ctx.last_clarification == ""


class TestPausedSessionModel:
    def test_model_creates_instance(self):
        """PausedSession se puede instanciar con los campos requeridos."""
        from core.models import PausedSession

        session = PausedSession(
            clarification_question="¿Test?",
            paused_state='{"subtask_index": 0}',
        )
        assert session.clarification_question == "¿Test?"
        assert session.paused_state == '{"subtask_index": 0}'
        assert session.clarification_answer is None
        assert session.resolved_at is None
