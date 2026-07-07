"""Tests for core/utils.py — clean_llm_response."""

from unittest.mock import MagicMock

from core.utils import clean_llm_response


class TestCleanLLMResponse:
    def test_choices_path(self):
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "  Hello world  "
        result = clean_llm_response(response)
        assert result == "Hello world"

    def test_message_attribute_path(self):
        response = MagicMock()
        del response.choices  # no choices attr
        response.message = MagicMock()
        response.message.content = "  Direct message  "
        result = clean_llm_response(response)
        assert result == "Direct message"

    def test_fallback_to_str(self):
        result = clean_llm_response("plain string")
        assert result == "plain string"

    def test_coroutine_detection(self):
        async def fake_coro():
            return "nope"

        result = clean_llm_response(fake_coro())
        assert "ERROR INTERNO" in result

    def test_attribute_error_fallback(self):
        response = object()
        result = clean_llm_response(response)
        assert isinstance(result, str)
        assert len(result) <= 800

    def test_removes_metadata_fields(self):
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = (
            "model=gpt-4 content='real content' created_at=12345 thinking=..."
        )
        result = clean_llm_response(response)
        assert "real content" in result
        assert "model=" not in result or "gpt-4" not in result
