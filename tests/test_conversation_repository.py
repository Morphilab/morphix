# tests/test_conversation_repository.py
"""Tests for ConversationRepository save, add_messages, list_all, export utilities."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models import Conversation, Message
from core.repositories.conversation_repository import (
    ConversationRepository,
    _collect_project_files,
    _strip_watermarks,
)


@pytest.fixture
def mock_session():
    """Simula una sesión asíncrona de BD."""
    session = MagicMock()
    session.get = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_get_async_session(mock_session):
    """Parchea get_async_session para devolver una sesión mock."""
    with patch("core.repositories.conversation_repository.get_async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__.return_value = mock_session
        mock_ctx.return_value.__aexit__.return_value = None
        yield mock_ctx


class TestSaveNewConversation:
    @pytest.mark.asyncio
    async def test_save_creates_new_conversation(self, mock_get_async_session, mock_session):
        """save() without conversation_id creates a new Conversation."""
        mock_session.get.return_value = None  # No existing conversation
        mock_session.flush = AsyncMock()
        conv_id = await ConversationRepository.save(
            title="Test query",
            user_message="Hello",
            tags="test",
            workflow_id=None,
            conversation_history=[{"role": "user", "content": "Hello"}],
            conversation_id=None,
        )
        # session.add was called with a Conversation object
        assert any(isinstance(c.args[0], Conversation) for c in mock_session.add.call_args_list)

    @pytest.mark.asyncio
    async def test_save_raises_on_empty_message(self, mock_get_async_session):
        """save() raises ValueError for empty user_message."""
        with pytest.raises(ValueError, match="user_message cannot be empty"):
            await ConversationRepository.save(
                title="Test",
                user_message="   ",
                tags="test",
            )


class TestSaveResumeConversation:
    @pytest.mark.asyncio
    async def test_save_resume_uses_existing_conversation(
        self, mock_get_async_session, mock_session
    ):
        """save() with conversation_id appends to existing conversation."""
        existing = Conversation(id=5, title="Original")
        mock_session.get.return_value = existing
        conv_id = await ConversationRepository.save(
            title="Follow-up",
            user_message="Another message",
            conversation_history=[{"role": "user", "content": "Another message"}],
            conversation_id=5,
        )
        # Should return the existing conversation id
        assert conv_id == 5

    @pytest.mark.asyncio
    async def test_save_resume_raises_on_missing_conversation(
        self, mock_get_async_session, mock_session
    ):
        """save() raises ValueError when conversation_id points to nonexistent."""
        mock_session.get.return_value = None
        with pytest.raises(ValueError, match="not found"):
            await ConversationRepository.save(
                title="Bad resume",
                user_message="msg",
                conversation_id=999,
            )


class TestNoDuplicateFirstMessage:
    @pytest.mark.asyncio
    async def test_first_user_message_not_duplicated(self, mock_get_async_session, mock_session):
        """The first user message appears only once, not twice."""
        mock_session.get.return_value = None
        await ConversationRepository.save(
            title="Test",
            user_message="First message",
            conversation_history=[
                {"role": "user", "content": "First message"},
                {"role": "assistant", "content": "Response"},
            ],
        )
        # Count how many Message objects with role="user" and content="First message" were added
        user_msg_adds = [
            c.args[0]
            for c in mock_session.add.call_args_list
            if isinstance(c.args[0], Message)
            and c.args[0].role == "user"
            and c.args[0].content == "First message"
        ]
        assert len(user_msg_adds) == 1, f"Expected 1 user message, got {len(user_msg_adds)}"

    @pytest.mark.asyncio
    async def test_system_messages_skipped(self, mock_get_async_session, mock_session):
        """System messages are never saved."""
        mock_session.get.return_value = None
        await ConversationRepository.save(
            title="Test",
            user_message="Query",
            conversation_history=[
                {"role": "system", "content": "You are an AI"},
                {"role": "user", "content": "Query"},
                {"role": "system", "content": "Be helpful"},
            ],
        )
        system_adds = [
            c.args[0]
            for c in mock_session.add.call_args_list
            if isinstance(c.args[0], Message) and c.args[0].role == "system"
        ]
        assert len(system_adds) == 0


class TestAddMessages:
    @pytest.mark.asyncio
    async def test_add_messages_appends(self, mock_get_async_session, mock_session):
        """add_messages() inserts Message records for existing conversation."""
        existing = Conversation(id=10, title="Test")
        mock_session.get.return_value = existing
        result = await ConversationRepository.add_messages(
            conv_id=10,
            messages=[
                {"role": "assistant", "content": "Response 1"},
                {"role": "assistant", "content": "Response 2"},
            ],
        )
        assert result is True
        # Count assistant Message adds
        assistant_adds = [
            c.args[0]
            for c in mock_session.add.call_args_list
            if isinstance(c.args[0], Message) and c.args[0].role == "assistant"
        ]
        assert len(assistant_adds) == 2

    @pytest.mark.asyncio
    async def test_add_messages_missing_conv(self, mock_get_async_session, mock_session):
        """add_messages() returns False when conversation doesn't exist."""
        mock_session.get.return_value = None
        result = await ConversationRepository.add_messages(999, [{"role": "user", "content": "x"}])
        assert result is False

    @pytest.mark.asyncio
    async def test_add_messages_empty(self, mock_get_async_session, mock_session):
        """add_messages() with empty list returns False."""
        result = await ConversationRepository.add_messages(1, [])
        assert result is False


class TestListAll:
    @pytest.mark.asyncio
    async def test_list_all_returns_dicts(self, mock_get_async_session, mock_session):
        """list_all() returns list of dicts sorted by created_at desc."""
        conv1 = Conversation(id=1, title="First")
        conv2 = Conversation(id=2, title="Second")
        result_mock = MagicMock()
        result_mock.scalars.return_value = [conv2, conv1]
        mock_session.execute.return_value = result_mock

        result = await ConversationRepository.list_all(limit=10, offset=0)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == 2
        assert result[0]["title"] == "Second"
        assert result[1]["id"] == 1


class TestStripWatermarks:
    def test_removes_watermark_style_ver(self):
        text = (
            "Esta es una respuesta muy larga que necesita tener más de cincuenta caracteres "
            "para que el watermark se haya aplicado.\n\n[ver.abc123def0]"
        )
        result = _strip_watermarks(text)
        assert "[ver." not in result

    def test_removes_watermark_style_trace(self):
        text = (
            "Otra respuesta larga que contiene información valiosa para el usuario "
            "y que tiene más de cincuenta caracteres.\n\n[trace:abc123def0]"
        )
        result = _strip_watermarks(text)
        assert "[trace:" not in result

    def test_removes_watermark_style_ref(self):
        text = (
            "Una respuesta larga con suficiente texto para que el sistema "
            "aplique la rotación de watermarks automáticamente.[ref:abc123def0]"
        )
        result = _strip_watermarks(text)
        assert "[ref:" not in result

    def test_removes_html_comment_watermark(self):
        text = (
            "Contenido largo de respuesta del asistente con más de cincuenta "
            "caracteres para probar.\n\n<!-- trace:abc123def0 -->"
        )
        result = _strip_watermarks(text)
        assert "<!-- trace:" not in result

    def test_preserves_normal_content(self):
        text = "Esta es una respuesta normal sin watermarks de ningún tipo."
        result = _strip_watermarks(text)
        assert result == text

    def test_removes_multiple_watermarks(self):
        text = (
            "Texto largo inicial para cumplir el umbral mínimo de cincuenta caracteres.\n\n"
            "[ver.abc123def0]\nOtra parte del texto que también es larga.\n\n"
            "[trace:fed456789a]"
        )
        result = _strip_watermarks(text)
        assert "[ver." not in result
        assert "[trace:" not in result


class TestCollectProjectFiles:
    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _collect_project_files(Path(tmpdir))
            assert result == ""

    def test_collects_python_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "main.py").write_text("print('hello')\n")
            (base / "README.md").write_text("# Readme\n")
            (base / "config.py").write_text("DEBUG = True\n")
            result = _collect_project_files(base)
            assert "main.py" in result
            assert "config.py" in result
            assert "print('hello')" in result
            # README.md no debe aparecer (solo .py, .yaml, .yml, .json, .env, .txt)
            assert "README.md" not in result

    def test_skips_hidden_and_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "__pycache__").mkdir()
            (base / "__pycache__" / "module.pyc").write_text("cached")
            (base / ".env").write_text("SECRET=1\n")
            result = _collect_project_files(base)
            assert "__pycache__" not in result
            assert ".env" in result  # .env is included

    def test_default_dir_same_as_memory_min(self):
        """Verifica que con un dir vacío devuelve cadena vacía."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "inexistente"
            result = _collect_project_files(base)
            assert result == ""
