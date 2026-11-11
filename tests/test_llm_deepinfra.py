# tests/test_llm_deepinfra.py
"""Proveedor DeepInfra (OpenAI-compatible) — rol aditivo ``deepinfra``.

Sin clave el rol cae a Ollama como cualquier proveedor sin clave; con clave
crea cliente OpenAI apuntando a la base de DeepInfra en los DOS caminos
(síncrono y streaming). La atribución del proveedor EFECTIVO
(``_effective_provider``) es parte del contrato: sin ella el breaker,
``provider_health`` y las métricas de latencia atribuyen a "ollama".
"""

import pytest


@pytest.fixture(autouse=True)
def _proveedor_fuera_de_offline(monkeypatch):
    """Los tests manejan el cliente vía settings explícitos; el modo offline
    del entorno redirige a un cliente Ollama real antes de llegar a los
    asserts — la decisión offline se fija en False."""
    from core.config import settings
    from llm.provider import LLMProvider

    monkeypatch.setattr(settings, "offline_mode", False)
    monkeypatch.setattr(LLMProvider._offline_manager, "is_offline", lambda: False)


MODELO = "deepseek-ai/DeepSeek-V4.1-Flash-1017"
BASE = "https://api.deepinfra.com/v1/openai"


class TestClienteDeepinfra:
    def test_rol_deepinfra_con_clave_crea_cliente_openai(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.deepinfra_api_key", "dfi-test")
        from openai import OpenAI

        from llm.provider import LLMProvider

        client, model, temp = LLMProvider.get_client("deepinfra")
        assert isinstance(client, OpenAI)
        assert str(client.base_url).startswith(BASE)
        assert model == MODELO
        assert temp == 0.7

    def test_rol_deepinfra_async_crea_cliente_asyncopenai(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.deepinfra_api_key", "dfi-test")
        from openai import AsyncOpenAI

        from llm.provider import LLMProvider

        client, model, _ = LLMProvider.get_async_client("deepinfra")
        assert isinstance(client, AsyncOpenAI)
        assert str(client.base_url).startswith(BASE)
        assert model == MODELO

    def test_sin_clave_deepinfra_cae_a_ollama(self, monkeypatch):
        # patch explícito a "": el .env real del usuario podría tener la clave
        monkeypatch.setattr("core.config.settings.deepinfra_api_key", "")
        import ollama

        from core.config import settings
        from llm.provider import LLMProvider

        client, model, _ = LLMProvider.get_client("deepinfra")
        assert isinstance(client, ollama.Client)
        assert model == settings.ollama_model

    def test_get_client_with_provider_reporta_deepinfra(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.deepinfra_api_key", "dfi-test")
        from llm.provider import LLMProvider

        *_otros, effective = LLMProvider.get_client_with_provider("deepinfra")
        assert effective == "deepinfra"

    def test_get_async_client_with_provider_reporta_deepinfra(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.deepinfra_api_key", "dfi-test")
        from llm.provider import LLMProvider

        *_otros, effective = LLMProvider.get_async_client_with_provider("deepinfra")
        assert effective == "deepinfra"


class TestProveedorEfectivo:
    def test_con_clave_es_deepinfra(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.deepinfra_api_key", "dfi-test")
        from llm.provider import LLMProvider

        assert LLMProvider._effective_provider("deepinfra") == "deepinfra"

    def test_sin_clave_es_ollama(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.deepinfra_api_key", "")
        from llm.provider import LLMProvider

        assert LLMProvider._effective_provider("deepinfra") == "ollama"

    def test_con_clave_pero_breaker_open_es_ollama(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.deepinfra_api_key", "dfi-test")
        from core.circuit_breaker import CircuitBreakerRegistry
        from llm.provider import LLMProvider

        breaker = CircuitBreakerRegistry.get("deepinfra")
        for _ in range(breaker.failure_threshold):
            breaker.record_failure()
        assert breaker.state == "open"
        assert LLMProvider._effective_provider("deepinfra") == "ollama"


class TestBreakerYOffline:
    def test_breaker_open_salta_a_ollama(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.deepinfra_api_key", "dfi-test")
        import ollama

        from core.circuit_breaker import CircuitBreakerRegistry
        from llm.provider import LLMProvider

        breaker = CircuitBreakerRegistry.get("deepinfra")
        for _ in range(breaker.failure_threshold):
            breaker.record_failure()
        client, model, _ = LLMProvider.get_client("deepinfra")
        assert isinstance(client, ollama.Client)
        assert model != MODELO

    def test_offline_forza_ollama(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.deepinfra_api_key", "dfi-test")
        monkeypatch.setattr("core.config.settings.offline_mode", True)
        from core.config import settings
        from llm.provider import LLMProvider

        assert LLMProvider.get_provider_name("deepinfra") == "ollama"
        client, model, _ = LLMProvider.get_client("deepinfra")
        assert model == settings.ollama_model
