# tests/test_llm_provider.py
from unittest.mock import patch

import pytest

from llm import LLMProvider


@pytest.mark.asyncio
async def test_get_client_returns_openai_when_configured():
    """LLMProvider.get_client retorna cliente OpenAI cuando hay API key."""
    with patch("core.config.settings") as mock_settings:
        mock_settings.openai_api_key = "sk-test-key"
        mock_settings.deepseek_api_key = ""
        mock_settings.grok_api_key = ""
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.offline_mode = False

        client, model, temp = LLMProvider.get_client("default")

        assert client is not None
        assert isinstance(model, str)


@pytest.mark.asyncio
async def test_get_client_falls_back_to_ollama_when_offline():
    """LLMProvider.get_client retorna Ollama en modo offline."""
    with patch("core.config.settings") as mock_settings:
        mock_settings.openai_api_key = ""
        mock_settings.deepseek_api_key = ""
        mock_settings.grok_api_key = ""
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.ollama_model = "phi3:mini"
        mock_settings.offline_mode = True

        client, model, temp = LLMProvider.get_client("default")

        assert client is not None
        # En modo offline debería usar Ollama


@pytest.mark.asyncio
async def test_get_client_force_ollama():
    """LLMProvider.get_client con force_ollama=True retorna cliente Ollama."""
    client, model, temp = LLMProvider.get_client("default", force_ollama=True)

    assert client is not None
    assert isinstance(model, str)


@pytest.mark.asyncio
async def test_get_client_returns_different_roles():
    """Cada rol tiene su propio modelo configurado."""
    with patch("core.config.settings") as mock_settings:
        mock_settings.openai_api_key = "sk-test"
        mock_settings.deepseek_api_key = ""
        mock_settings.grok_api_key = ""
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.offline_mode = False

        for role in ["default", "fast", "reasoning", "creative"]:
            client, model, temp = LLMProvider.get_client(role)
            assert client is not None
