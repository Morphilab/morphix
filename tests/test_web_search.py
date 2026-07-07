# tests/test_web_search.py
"""Tests para la herramienta de búsqueda web."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_web_search_no_api_key():
    """Verifica que sin API key retorna mensaje de configuración."""
    from tools.web_search import _web_search_tool

    with patch("tools.web_search.settings") as mock_settings:
        mock_settings.google_api_key = ""
        mock_settings.google_cx = ""
        result = await _web_search_tool("test query")
        assert "no configurado" in result.lower()


@pytest.mark.asyncio
async def test_web_search_with_results():
    """Verifica que una búsqueda exitosa retorna resultados formateados."""
    from tools.web_search import _web_search_tool

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [
            {"title": "Test Title", "snippet": "Test snippet", "link": "https://example.com"},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with (
        patch("tools.web_search.settings") as mock_settings,
        patch("tools.web_search.httpx.AsyncClient") as mock_client,
    ):
        mock_settings.google_api_key = "test-key"
        mock_settings.google_cx = "test-cx"
        mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

        result = await _web_search_tool("test query")
        assert "Test Title" in result
        assert "https://example.com" in result


@pytest.mark.asyncio
async def test_web_search_handles_api_error():
    """Verifica que errores de API se manejan gracefulmente."""
    from tools.web_search import _web_search_tool

    with (
        patch("tools.web_search.settings") as mock_settings,
        patch("tools.web_search.httpx.AsyncClient") as mock_client,
    ):
        mock_settings.google_api_key = "test-key"
        mock_settings.google_cx = "test-cx"
        mock_client.return_value.__aenter__.return_value.get.side_effect = RuntimeError("API error")

        result = await _web_search_tool("test query")
        assert "Error" in result
