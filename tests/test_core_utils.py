"""Tests for core/utils.py — clean_llm_response."""

from unittest.mock import MagicMock

from core.utils import build_child_env, clean_llm_response


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


class TestBuildChildEnv:
    def test_build_child_env_denies_secrets(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
        monkeypatch.setenv("ENCRYPTION_KEY", "x" * 32)
        monkeypatch.setenv("PATH", "/usr/bin")
        env = build_child_env(home="/tmp/ws")
        assert "DEEPSEEK_API_KEY" not in env
        assert "DATABASE_URL" not in env
        assert "ENCRYPTION_KEY" not in env
        assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
        assert env["HOME"] == "/tmp/ws"

    def test_build_child_env_keeps_locale_and_term(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.setenv("LC_ALL", "es_ES.UTF-8")
        env = build_child_env()
        assert env["TERM"] == "xterm-256color"
        assert env["LC_ALL"] == "es_ES.UTF-8"

    def test_build_child_env_denies_unknown_vars(self, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", "/somewhere")
        from core.utils import build_child_env

        assert "VIRTUAL_ENV" not in build_child_env()

    def test_build_child_env_home_none_keeps_inherited(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/real")
        from core.utils import build_child_env

        assert build_child_env()["HOME"] == "/home/real"


class TestChildEnvPathConfigurable:
    """PATH de hijos configurable vía MORPHIX_CHILD_ENV_PATH — el
    hardcode excluye venvs/`~/.local/bin`; el default se conserva."""

    def test_default_unchanged(self, monkeypatch):
        monkeypatch.delenv("MORPHIX_CHILD_ENV_PATH", raising=False)
        env = build_child_env()
        assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"

    def test_override_picked_up(self, monkeypatch):
        custom = "/opt/morphix/venv/bin:/usr/local/bin:/usr/bin:/bin"
        monkeypatch.setenv("MORPHIX_CHILD_ENV_PATH", custom)
        env = build_child_env()
        assert env["PATH"] == custom

    def test_override_applies_to_git_guard_too(self, monkeypatch):
        """El guard de GitPython comparte la misma allowlist."""
        from tools.git_manager import _git_env_guard

        custom = "/custom/bin:/usr/bin"
        monkeypatch.setenv("MORPHIX_CHILD_ENV_PATH", custom)
        with _git_env_guard():
            import os

            assert os.environ["PATH"] == custom
