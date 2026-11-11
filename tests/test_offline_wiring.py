"""Una única fuente de verdad para el estado offline.

Bootstrap daemon, toggle GUI y provider comparten la MISMA instancia de
OfflineManager: si el estado detectado no llegara al provider, una caída
de red en arranque no enrutaría a Ollama automáticamente.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_instances_share_detected_state(monkeypatch):
    """detect() en una instancia debe verse desde OTRAS instancias."""
    from llm.offline import OfflineManager

    monkeypatch.setattr("core.config.settings.offline_mode", False)

    # Simular red caída: todos los endpoints fallan
    fake_client = AsyncMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(side_effect=Exception("red caída"))

    with patch("llm.offline.httpx.AsyncClient", return_value=fake_client):
        om1 = OfflineManager()
        detected = await om1.detect()

    assert detected is True

    om2 = OfflineManager()
    assert (
        om2.is_offline() is True
    ), "el estado detectado no cruza instancias (atributo de instancia)"


@pytest.mark.asyncio
async def test_provider_routes_to_ollama_after_failed_detect(monkeypatch):
    """Caída de red en arranque → get_provider_name enruta a ollama."""
    import llm.provider as prov_mod
    from llm.offline import OfflineManager

    monkeypatch.setattr("core.config.settings.offline_mode", False)

    fake_client = AsyncMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(side_effect=Exception("red caída"))

    with (
        patch("llm.offline.httpx.AsyncClient", return_value=fake_client),
        patch.dict(
            "os.environ",
            {
                "DEEPSEEK_API_KEY": "sk-x",
                "OFFLINE_MODE": "false",
                "MODEL_ROLES": "",  # usar defaults del archivo
            },
        ),
    ):
        # Reset del estado de clase entre escenarios
        OfflineManager._state_offline = None
        om = OfflineManager()
        await om.detect()
        name = prov_mod.LLMProvider.get_provider_name("agent")

    assert name == "ollama", f"provider ignoró la detección offline ({name})"


@pytest.mark.asyncio
async def test_toggle_reflects_across_instances():
    """toggle_offline en una instancia se ve en otra."""
    from core.config import settings
    from llm.offline import OfflineManager

    original = settings.offline_mode
    try:
        om1 = OfflineManager()
        om2 = OfflineManager()
        new_state = om1.toggle_offline()
        assert om2.is_offline() == new_state
    finally:
        settings.offline_mode = original
