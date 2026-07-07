"""Tests for core/models.py — ORM model instantiation, _utc_now."""

from core.models import Conversation, Message, User, Workflow, _utc_now


class TestUtcNow:
    def test_returns_datetime(self):
        result = _utc_now()
        from datetime import datetime

        assert isinstance(result, datetime)
        assert result.tzinfo is None  # stripped per impl


class TestConversation:
    def test_minimal_instantiation(self):
        conv = Conversation(title="Test")
        assert conv.title == "Test"
        assert conv.tags is None

    def test_field_types(self):
        conv = Conversation(title="Hi", tags="tag1,tag2")
        assert isinstance(conv.id, int | None)
        assert isinstance(conv.title, str)


class TestMessage:
    def test_minimal_instantiation(self):
        msg = Message(conversation_id=1, role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"
        assert msg.conversation_id == 1


class TestWorkflow:
    def test_status_default(self):
        wf = Workflow(query="test", subtasks="[]")
        assert wf.status == "pending"

    def test_scorecard_nullable(self):
        wf = Workflow(query="test", subtasks="[]", scorecard="ok")
        assert wf.scorecard == "ok"


class TestUser:
    def test_minimal_instantiation(self):
        user = User(username="testuser", password_hash="hash123")
        assert user.username == "testuser"
        assert user.password_hash == "hash123"

    def test_username_unique(self):
        u = User(username="unique_user", password_hash="x")
        assert u.username == "unique_user"
