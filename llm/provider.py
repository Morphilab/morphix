# llm/provider.py
import logging
import time

import httpx

from core.circuit_breaker import CircuitBreakerRegistry
from core.config import settings
from core.constants import DEFAULT_PROVIDER_NAME
from llm.offline import OfflineManager

logger = logging.getLogger(__name__)


def _read_timeout(connect_timeout: int) -> int:
    """Read-timeout explícito (LLM_READ_TIMEOUT); sin él, el derivado
    connect*3 de siempre."""
    return int(getattr(settings, "llm_read_timeout", 0) or 0) or connect_timeout * 3


def _http_client(connect_timeout: int) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(
            connect=connect_timeout, read=_read_timeout(connect_timeout), write=30, pool=5
        ),
    )


def _http_async_client(connect_timeout: int) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=connect_timeout, read=_read_timeout(connect_timeout), write=30, pool=5
        ),
    )


def nvidia_key_for_model(model: str) -> str | None:
    """Resuelve la clave del POOL NVIDIA para el modelo dado.

    El pool se declara con claves numeradas en el entorno, cada una ligada a
    un modelo: ``NVIDIA_API_KEY_<n>`` emparejada con ``NVIDIA_MODEL_<n>``
    (mismo índice). Reglas de resolución:

    1. Emparejamiento exacto: ``NVIDIA_MODEL_<n> == model`` → esa clave.
    2. Sin emparejamientos declarados: si hay UNA sola clave (numerada o la
       base ``NVIDIA_API_KEY``) se usa para cualquier modelo — el caso de un
       solo modelo en el pool.
    3. Varias claves SIN emparejar: la primera, con warning — el pool es
       ambiguo y probablemente falta declarar los ``NVIDIA_MODEL_<n>``.
    4. Sin ninguna clave: None (el proveedor queda no disponible y la
       selección cae a Ollama, como el resto de proveedores sin clave).
    """
    import re as _re

    from core.config import nvidia_pool_entries

    entradas = nvidia_pool_entries()
    pares: dict[str, str] = {}
    sueltas: list[tuple[int, str]] = []
    for nombre, valor in entradas.items():
        m = _re.fullmatch(r"NVIDIA_API_KEY_(\d+)", nombre)
        if not m or not valor.strip():
            continue
        idx = m.group(1)
        modelo = entradas.get(f"NVIDIA_MODEL_{idx}", "").strip()
        clave = valor.strip()
        if modelo:
            pares[modelo] = clave
        else:
            sueltas.append((int(idx), clave))
    if model in pares:
        return pares[model]
    total = len(pares) + len(sueltas)
    if total == 0:
        return settings.nvidia_api_key or None
    if total == 1:
        if sueltas:
            return sueltas[0][1]
        return next(iter(pares.values()))
    if sueltas:
        sueltas.sort()
        logger.warning(
            "Pool NVIDIA ambiguo: %d claves sin NVIDIA_MODEL_<n> — usando la "
            "primera; declara el emparejamiento para enrutar por modelo",
            len(sueltas),
        )
        return sueltas[0][1]
    # hay pares pero ninguno para ESTE modelo
    logger.warning("Pool NVIDIA sin clave para el modelo %r (pares: %s)", model, sorted(pares))
    return None


def _nvidia_pool_disponible() -> bool:
    """¿Hay ALGUNA clave NVIDIA? Para el chequeo de disponibilidad por
    proveedor; la resolución fina por modelo ocurre al crear el cliente."""
    if settings.nvidia_api_key:
        return True
    from core.config import nvidia_pool_entries

    return any(
        nombre.startswith("NVIDIA_API_KEY_") and valor.strip()
        for nombre, valor in nvidia_pool_entries().items()
    )


class LLMProvider:
    _offline_manager = OfflineManager()

    @classmethod
    def get_provider_name(cls, role: str) -> str:
        """Devuelve el nombre del proveedor para un rol dado.

        Contempla el modo offline: aunque el rol esté configurado con un
        proveedor en la nube (p.ej. deepseek), si el sistema está offline la
        llamada real va a Ollama, así que el proveedor efectivo es "ollama".
        """
        if settings.offline_mode or cls._offline_manager.is_offline():
            return "ollama"
        role_config = settings.model_roles.get(role, settings.model_roles["default"])
        return role_config.get("provider", DEFAULT_PROVIDER_NAME)

    @classmethod
    def _effective_provider(cls, provider: str) -> str:
        """Resuelve el proveedor EFECTIVO sin consumir sonda del breaker."""
        key_map = {
            "deepseek": settings.deepseek_api_key,
            "openai": settings.openai_api_key,
            "grok": settings.grok_api_key,
            "nvidia": _nvidia_pool_disponible(),
            "deepinfra": settings.deepinfra_api_key,
        }
        has_key = bool(key_map.get(provider))
        breaker = CircuitBreakerRegistry.get(provider)
        breaker_open_now = breaker.state == "open" and (
            time.time() - breaker._last_failure_time < breaker.recovery_timeout
        )
        return provider if (has_key and not breaker_open_now) else "ollama"

    @classmethod
    def get_client_with_provider(
        cls, role: str, temperature: float | None = None, force_ollama: bool = False
    ):
        """Como get_client pero retorna el PROVEEDOR EFECTIVO usado.

        Returns: (client, model, temp, effective_provider)
        """
        offline = force_ollama or settings.offline_mode or cls._offline_manager.is_offline()
        if offline:
            return (*cls._create_ollama_client(role, temperature), "ollama")

        role_config = settings.model_roles.get(role, settings.model_roles["default"])
        provider = role_config.get("provider", DEFAULT_PROVIDER_NAME)
        client_model_temp = cls.get_client(role, temperature)
        return (*client_model_temp, cls._effective_provider(provider))

    @classmethod
    def get_async_client_with_provider(
        cls, role: str, temperature: float | None = None, force_ollama: bool = False
    ):
        """Variante async de get_client_with_provider."""
        offline = force_ollama or settings.offline_mode or cls._offline_manager.is_offline()
        if offline:
            return (*cls._create_ollama_client(role, temperature), "ollama")

        role_config = settings.model_roles.get(role, settings.model_roles["default"])
        provider = role_config.get("provider", DEFAULT_PROVIDER_NAME)
        client_model_temp = cls.get_async_client(role, temperature)
        return (*client_model_temp, cls._effective_provider(provider))

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
        provider = role_config.get("provider", DEFAULT_PROVIDER_NAME)
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

        # NVIDIA integrate (OpenAI-compatible, pool de claves por modelo)
        if provider == "nvidia":
            nvidia_key = nvidia_key_for_model(model)
            if nvidia_key and CircuitBreakerRegistry.get("nvidia").allow_request():
                from openai import OpenAI

                return (
                    OpenAI(
                        api_key=nvidia_key,
                        base_url=settings.nvidia_api_base,
                        http_client=_http_client(settings.llm_timeout),
                    ),
                    model,
                    temp,
                )
            if not nvidia_key:
                logger.warning("NVIDIA sin clave para el modelo %r — cayendo a Ollama", model)

        # DeepInfra (OpenAI-compatible)
        if provider == "deepinfra" and settings.deepinfra_api_key:
            if not CircuitBreakerRegistry.get("deepinfra").allow_request():
                logger.warning("Circuit breaker OPEN for deepinfra, skipping provider")
            else:
                from openai import OpenAI

                return (
                    OpenAI(
                        api_key=settings.deepinfra_api_key,
                        base_url=settings.deepinfra_api_base,
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
        provider = role_config.get("provider", DEFAULT_PROVIDER_NAME)
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

        # NVIDIA integrate (paridad síncrona: streaming usa el mismo pool)
        if provider == "nvidia":
            nvidia_key = nvidia_key_for_model(model)
            if nvidia_key and CircuitBreakerRegistry.get("nvidia").allow_request():
                from openai import AsyncOpenAI

                return (
                    AsyncOpenAI(
                        api_key=nvidia_key,
                        base_url=settings.nvidia_api_base,
                        http_client=_http_async_client(settings.llm_timeout),
                    ),
                    model,
                    temp,
                )
            if not nvidia_key:
                logger.warning("NVIDIA sin clave para el modelo %r — cayendo a Ollama", model)

        # DeepInfra (paridad síncrona: streaming usa el mismo proveedor)
        if provider == "deepinfra" and settings.deepinfra_api_key:
            if not CircuitBreakerRegistry.get("deepinfra").allow_request():
                logger.warning("Circuit breaker OPEN for deepinfra, skipping provider")
            else:
                from openai import AsyncOpenAI

                return (
                    AsyncOpenAI(
                        api_key=settings.deepinfra_api_key,
                        base_url=settings.deepinfra_api_base,
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
        ollama_model = role_config.get("ollama_model") or settings.ollama_model
        temp = temperature if temperature is not None else role_config.get("temperature", 0.7)
        logger.info(f"Usando Ollama con modelo '{ollama_model}' (rol: {role})")
        import ollama

        client = ollama.Client(host=settings.ollama_base_url, timeout=settings.llm_timeout)
        return client, ollama_model, temp
