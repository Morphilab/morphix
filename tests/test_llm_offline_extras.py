"""Additional tests for llm/offline.py — detect failure, is_offline, toggle."""

from unittest.mock import AsyncMock, patch

import pytest

from llm.offline import OfflineManager


class TestOfflineManager:
    @pytest.mark.asyncio
    async def test_detect_all_endpoints_fail(self):
        om = OfflineManager()
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.side_effect = ConnectionError("no network")

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await om.detect()
            assert result is True
            assert om._is_offline is True

    def test_is_offline_when_forced(self):
        om = OfflineManager()
        om._is_offline = False
        from core.config import settings

        original = settings.offline_mode
        settings.offline_mode = True
        try:
            assert om.is_offline() is True
        finally:
            settings.offline_mode = original

    def test_is_offline_when_detected(self):
        om = OfflineManager()
        om._is_offline = True
        assert om.is_offline() is True

    def test_toggle_offline_on(self):
        om = OfflineManager()
        from core.config import settings

        original = settings.offline_mode
        settings.offline_mode = False
        try:
            result = om.toggle_offline()
            assert result is True
            assert settings.offline_mode is True
            assert om._is_offline is True
        finally:
            settings.offline_mode = original

    def test_toggle_offline_off(self):
        om = OfflineManager()
        from core.config import settings

        original = settings.offline_mode
        settings.offline_mode = True
        try:
            result = om.toggle_offline()
            assert result is False
            assert settings.offline_mode is False
            assert om._is_offline is False
        finally:
            settings.offline_mode = original
