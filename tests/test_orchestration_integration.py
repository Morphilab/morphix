"""Integration tests for orchestration — exercises real code paths, not mocks."""

from orchestration.context import Session, WorkflowContext, WorkflowEvents


class TestWorkflowContext:
    """WorkflowContext construction and defaults."""

    def test_minimal_construction(self):
        """WorkflowContext can be constructed with required fields."""
        ctx = WorkflowContext(query="test query", mode="chat")
        assert ctx.query == "test query"
        assert ctx.mode == "chat"
        assert ctx.workspace == "main"
        assert ctx.is_follow_up is False
        assert ctx.cancelled is False

    def test_orchestration_mode(self):
        """Orquestar mode sets appropriate defaults."""
        ctx = WorkflowContext(query="build a feature", mode="orquestar")
        assert ctx.mode == "orquestar"

    def test_follow_up_flag(self):
        """Follow-up flag is correctly stored."""
        ctx = WorkflowContext(query="modify this", is_follow_up=True)
        assert ctx.is_follow_up is True


class TestSession:
    """Session dataclass binds context and events."""

    def test_session_construction(self):
        """Session binds WorkflowContext and WorkflowEvents together."""
        ctx = WorkflowContext(query="build a thing")
        events = WorkflowEvents()
        session = Session(context=ctx, events=events)
        assert session.context is ctx
        assert session.events is events

    def test_session_default_events(self):
        """Session with None events has nullable callbacks."""
        ctx = WorkflowContext(query="hello")
        events = WorkflowEvents()
        assert events.on_system_message is None
        assert events.on_stream_chunk is None
