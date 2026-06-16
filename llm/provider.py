# llm/provider.py
import logging

import httpx

from core.circuit_breaker import CircuitBreakerRegistry
from core.config import settings
from llm.offline import OfflineManager

logger = logging.getLogger(__name__)


def _http_client(connect_timeout: int) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(connect=connect_timeout, read=connect_timeout * 3, write=30, pool=5),
    )


def _http_async_client(connect_timeout: int) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=connect_timeout, read=connect_timeout * 3, write=30, pool=5),
    )


class LLMProvider:
    _offline_manager = OfflineManager()

    @classmethod
    def get_provider_name(cls, role: str) -> str:
        """Devuelve el nombre del proveedor para un rol dado."""
        role_config = settings.model_roles.get(role, settings.model_roles["default"])
        return role_config.get("provider", "deepseek")

    @classmethod
    def get_client(cls, role: str, temperature: float | None = None, force_ollama: bool = False):
        """
        Returns a configured OpenAI client for the given role.
        If force_ollama=True or the system is offline, returns an Ollama client.
        """
        offline = force_ollama or settings.offline_mode or cls._offline_manager.is_offline()

        if offline:
            return cls._create_ollama_client(role, temperature)

        # Try online providers according to configuration
        role_config = settings.model_roles.get(role, settings.model_roles["default"])
        provider = role_config.get("provider", "deepseek")
        model = role_config["model"]
        temp = temperature if temperature is not None else role_config.get("temperature", 0.7)

        # DeepSeek
        if provider == "deepseek" and settings.deepseek_api_key:
            if not CircuitBreakerRegistry.get("deepseek").allow_request():
                logger.warning("Circuit breaker OPEN for deepseek, skipping provider")
            else:
                from openai import OpenAI

                deepseek_base = (
                    f"{settings.deepseek_api_base}/beta"
                    if settings.deepseek_strict_mode
                    else settings.deepseek_api_base
                )
                return (
                    OpenAI(
                        api_key=settings.deepseek_api_key,
                        base_url=deepseek_base,
                        http_client=_http_client(settings.llm_timeout),
                    ),
                    model,
                    temp,
                )

        # OpenAI
        if provider == "openai" and settings.openai_api_key:
            if not CircuitBreakerRegistry.get("openai").allow_request():
                logger.warning("Circuit breaker OPEN for openai, skipping provider")
            else:
                from openai import OpenAI

                return (
                    OpenAI(
                        api_key=settings.openai_api_key,
                        http_client=_http_client(settings.llm_timeout),
                    ),
                    model,
                    temp,
                )

        # Grok
        if provider == "grok" and settings.grok_api_key:
            if not CircuitBreakerRegistry.get("grok").allow_request():
                logger.warning("Circuit breaker OPEN for grok, skipping provider")
            else:
                from openai import OpenAI

                return (
                    OpenAI(
                        api_key=settings.grok_api_key,
                        base_url=settings.grok_api_base,
                        http_client=_http_client(settings.llm_timeout),
                    ),
                    model,
                    temp,
                )

        # If no online provider found, force Ollama
        return cls._create_ollama_client(role, temperature)

    @classmethod
    def get_async_client(
        cls, role: str, temperature: float | None = None, force_ollama: bool = False
    ):
        """Devuelve un cliente AsyncOpenAI para streaming no bloqueante.
        Si force_ollama=True o el sistema está offline, devuelve cliente Ollama (ya async-safe)."""
        offline = force_ollama or settings.offline_mode or cls._offline_manager.is_offline()

        if offline:
            return cls._create_ollama_client(role, temperature)

        role_config = settings.model_roles.get(role, settings.model_roles["default"])
        provider = role_config.get("provider", "deepseek")
        model = role_config["model"]
        temp = temperature if temperature is not None else role_config.get("temperature", 0.7)

        # DeepSeek
        if provider == "deepseek" and settings.deepseek_api_key:
            if not CircuitBreakerRegistry.get("deepseek").allow_request():
                logger.warning("Circuit breaker OPEN for deepseek, skipping provider")
            else:
                from openai import AsyncOpenAI

                deepseek_base = (
                    f"{settings.deepseek_api_base}/beta"
                    if settings.deepseek_strict_mode
                    else settings.deepseek_api_base
                )
                return (
                    AsyncOpenAI(
                        api_key=settings.deepseek_api_key,
                        base_url=deepseek_base,
                        http_client=_http_async_client(settings.llm_timeout),
                    ),
                    model,
                    temp,
                )

        # OpenAI
        if provider == "openai" and settings.openai_api_key:
            if not CircuitBreakerRegistry.get("openai").allow_request():
                logger.warning("Circuit breaker OPEN for openai, skipping provider")
            else:
                from openai import AsyncOpenAI

                return (
                    AsyncOpenAI(
                        api_key=settings.openai_api_key,
                        http_client=_http_async_client(settings.llm_timeout),
                    ),
                    model,
                    temp,
                )

        # Grok
        if provider == "grok" and settings.grok_api_key:
            if not CircuitBreakerRegistry.get("grok").allow_request():
                logger.warning("Circuit breaker OPEN for grok, skipping provider")
            else:
                from openai import AsyncOpenAI

                return (
                    AsyncOpenAI(
                        api_key=settings.grok_api_key,
                        base_url=settings.grok_api_base,
                        http_client=_http_async_client(settings.llm_timeout),
                    ),
                    model,
                    temp,
                )

        # If no online provider found, force Ollama
        return cls._create_ollama_client(role, temperature)

    @classmethod
    def _create_ollama_client(cls, role: str, temperature: float | None = None):
        """Crea un cliente Ollama usando la configuración de model_roles."""
        role_config = settings.model_roles.get(role, settings.model_roles["default"])
        ollama_model = settings.ollama_model or role_config.get("model", "phi3:mini")
        temp = temperature if temperature is not None else role_config.get("temperature", 0.7)
        logger.info(f"Usando Ollama con modelo '{ollama_model}' (rol: {role})")
        import ollama

        client = ollama.Client(host=settings.ollama_base_url)
        return client, ollama_model, temp
