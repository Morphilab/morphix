"""
Configuración global de Morphix — Settings con pydantic-settings.
"""

import base64
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    """Configuración global de Morphix"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    dark_mode: bool = Field(
        default=True,
        validation_alias="DARK_MODE",
        description="Tema de la GUI: true = oscuro (papel), false = claro (papel claro)",
    )
    offline_mode: bool = Field(default=False, validation_alias="OFFLINE_MODE")

    # API Keys
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    grok_api_key: str = Field(default="", validation_alias="GROK_API_KEY")
    # NVIDIA integrate (OpenAI-compatible). La clave base es conveniencia para
    # un solo modelo; el POOL se declara con claves numeradas en el entorno:
    # NVIDIA_API_KEY_<n> emparejada con NVIDIA_MODEL_<n> (ver
    # llm/provider.nvidia_key_for_model).
    nvidia_api_key: str = Field(default="", validation_alias="NVIDIA_API_KEY")
    nvidia_api_base: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        validation_alias="NVIDIA_API_BASE",
    )
    deepseek_api_base: str = Field(
        default="https://api.deepseek.com",
        validation_alias="DEEPSEEK_API_BASE",
        description="DeepSeek API base URL. Change for proxies or self-hosted deployments.",
    )
    grok_api_base: str = Field(
        default="https://api.x.ai/v1",
        validation_alias="GROK_API_BASE",
        description="Grok API base URL. Change for proxies or self-hosted deployments.",
    )
    # DeepInfra (OpenAI-compatible). Una sola clave sirve chat y embeddings
    # (los embeddings van por EMBED_OPENAI_* con esta misma clave).
    deepinfra_api_key: str = Field(default="", validation_alias="DEEPINFRA_API_KEY")
    deepinfra_api_base: str = Field(
        default="https://api.deepinfra.com/v1/openai",
        validation_alias="DEEPINFRA_API_BASE",
        description="DeepInfra OpenAI-compatible base URL. Change for proxies or self-hosted deployments.",
    )
    hf_token: str = Field(
        default="",
        validation_alias="HF_TOKEN",
        description="HuggingFace API token. Consumed by sentence-transformers/huggingface_hub for model downloads. Without it, anonymous rate limits apply.",
    )

    # Ollama y base de datos
    ollama_base_url: str = Field(
        default="http://localhost:11434", validation_alias="OLLAMA_BASE_URL"
    )
    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    ollama_model: str = Field(default="phi3:mini", validation_alias="OLLAMA_MODEL")
    llm_timeout: int = Field(
        default=60,
        validation_alias="LLM_TIMEOUT",
        description="Timeout for LLM HTTP client connections (used by provider.py).",
    )
    # Read-timeout del cliente LLM: una conexión muerta colgaba el paso
    # hasta 10 min (read = connect*3 derivado de llm_timeout). 300s corta
    # la hemorragia sin matar llamadas de reasoning legítimas (una ventana
    # degradada puede tardar varios minutos).
    llm_read_timeout: int = Field(default=300, validation_alias="LLM_READ_TIMEOUT")
    deepseek_strict_mode: bool = Field(
        default=False,
        validation_alias="DEEPSEEK_STRICT_MODE",
        description="Activar strict mode de DeepSeek para forzar respeto de required en tools",
    )
    llm_strict_schema_providers: str = Field(
        default="",
        validation_alias="LLM_STRICT_SCHEMA_PROVIDERS",
        description=(
            "CSV de proveedores con strict tool schemas (deepseek, deepinfra, nvidia, ...). "
            "Vacío = semántica legacy: DEEPSEEK_STRICT_MODE aplica a todos los OpenAI-compat."
        ),
    )
    max_context_tokens: int = Field(
        default=128000,
        validation_alias="MAX_CONTEXT_TOKENS",
        description="Tokens máximos de contexto del modelo",
    )
    prompt_cache_preserve_prefix: bool = Field(
        default=True,
        validation_alias="PROMPT_CACHE_PRESERVE_PREFIX",
        description=(
            "Compresión cache-first: preserva el prefijo cacheable del transcript "
            "(system + primeras vueltas) para maximizar prompt-cache del proveedor"
        ),
    )
    llm_retry_durable: bool = Field(
        default=True,
        validation_alias="LLM_RETRY_DURABLE",
        description=(
            "Contador de reintentos LLM persistido en disco (sobrevive crashes y "
            "evita crash-loops con fallos deterministas)"
        ),
    )

    # Sin `redis_url`: no hay caché Redis — CacheManager es telemetría de
    # prompt-cache, no una caché. Si algún día se cablea una caché real,
    # vuelve aquí con su funcionalidad.

    # Default agent names
    default_agent: str = Field(
        default="developer", validation_alias="DEFAULT_AGENT", description="Default agent"
    )
    fallback_agent: str = Field(
        default="conversacional", validation_alias="FALLBACK_AGENT", description="Fallback agent"
    )

    # Seguridad
    encryption_key: str = Field(default="", validation_alias="ENCRYPTION_KEY")
    password_hash: str = Field(default="", validation_alias="PASSWORD_HASH")

    # ==================== MODEL ROLES (centralizado) ====================
    model_roles: dict[str, dict[str, Any]] = Field(
        default_factory=lambda: {
            # Todos los roles de producto corren en DeepInfra. Los demás
            # proveedores (NVIDIA pool, DeepSeek, Grok, OpenAI) permanecen
            # soportados en LLMProvider; sus tests ejercen el proveedor con
            # roles sintéticos, no con los defaults de producto.
            "default": {
                "provider": "deepinfra",
                "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
                "temperature": 0.7,
                "max_tokens": 4096,
                "tool_calling": True,
            },
            "fast": {
                "provider": "deepinfra",
                "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
                "temperature": 0.3,
                # Mínimo 1024: con 512 el reasoning agota el presupuesto y la
                # respuesta llega vacía (starvation).
                # El reasoning se contiene con retry y no reenviando
                # reasoning_content.
                "max_tokens": 1024,
                "tool_calling": True,
            },
            "reasoning": {
                "provider": "deepinfra",
                "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
                "temperature": 0.0,
                "max_tokens": 4096,
                "tool_calling": True,
            },
            "agent": {
                "provider": "deepinfra",
                "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
                "temperature": 0.7,
                # Thinking model: el reasoning_content comparte este cap y
                # puede consumirlo COMPLETO en pasos exigentes sin entregar
                # contenido. Debe dejar margen para razonar Y entregar; el
                # starve-retry del loop duplica ante un 'length' residual.
                "max_tokens": 8192,
                "tool_calling": True,
            },
            "creative": {
                "provider": "deepinfra",
                "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
                "temperature": 0.9,
                "max_tokens": 4096,
                "tool_calling": True,
            },
            "critique": {
                "provider": "deepinfra",
                "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
                "temperature": 0.0,
                "max_tokens": 1024,
                "tool_calling": True,
            },
            # Visión: análisis one-shot de imágenes vía tool vision_analyze.
            # tool_calling False: el análisis es una sola pasada sin tools.
            # ollama_model: el fallback local exige un modelo con visión.
            "vision": {
                "provider": "deepinfra",
                "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
                "ollama_model": "qwen2.5vl:7b",
                "temperature": 0.3,
                "max_tokens": 2048,
                "tool_calling": False,
            },
            # Rol canónico al que apuntan los templates de agentes con
            # model_role. Thinking model: el reasoning_content comparte el
            # cap de max_tokens y el starve-retry del controller lo duplica
            # ante finish='length'. max_tokens 8192 = paridad con 'agent'.
            "deepinfra": {
                "provider": "deepinfra",
                "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
                "temperature": 0.7,
                "max_tokens": 8192,
                "tool_calling": True,
            },
        },
        description=(
            "Model configuration by role. Configure in code (core/config.py). "
            "Not settable via .env. tool_calling (default True): disable for models "
            "that don't support native function calling. "
            "ollama_model (optional): Ollama model name for this role when offline/fallback. "
            "Falls back to OLLAMA_MODEL env var if not set."
        ),
    )

    # Capacidades conocidas por modelo (tool calling). Los modelos no
    # listados se asumen capaces (default optimista) — la degradación
    # real se detecta dinámicamente vía telemetría de repairs.
    # SOLO se documentan entradas verificadas con soporte nativo (tools:
    # True). NO añadir entradas tools:False sin una verificación previa:
    # un síntoma de "modelo sin tools" suele ser un bug del parser, no
    # una falta real de tool calling (verificar docs del proveedor).
    model_capabilities: dict[str, dict[str, bool]] = Field(
        default_factory=lambda: {
            "qwen2.5-coder:7b": {"tools": True},
            "qwen3.5:9b": {"tools": True},
            "deepseek-r1:8b": {"tools": True},
            # Tool-calling nativo con args string e id.
            "deepseek-ai/DeepSeek-V4.1-Flash-1017": {"tools": True},
        },
        description=(
            "Known per-model capabilities. In code (core/config.py), "
            "not settable via .env. 'tools': False disables native "
            "function calling for that model."
        ),
    )

    @model_validator(mode="after")
    def ensure_encryption_key(self) -> "Settings":
        """Valida que encryption_key exista. En producción lanza error, en desarrollo auto-genera."""
        is_production = os.getenv("MORPHIX_ENV") == "production"
        if not self.encryption_key:
            if is_production:
                logger.critical(
                    "🔑 ENCRYPTION_KEY no configurada en .env. "
                    "En producción es obligatoria. "
                    'Genera una con: python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
                )
                raise ValueError(
                    "ENCRYPTION_KEY es obligatoria en producción. "
                    "Configúrala en tu archivo .env."
                )
            generated = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")
            self.encryption_key = generated
            logger.warning(
                "🔑 ENCRYPTION_KEY no configurada en .env. Se generó una clave temporal "
                "(solo en desarrollo)."
            )
            logger.warning(
                "⚠️  GUARDA UNA CLAVE en tu .env como ENCRYPTION_KEY=<clave> "
                "o perderás datos encriptados al reiniciar. "
                'Genera una con: python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
            )
        else:
            try:
                key_bytes = self.encryption_key.encode()
                Fernet(
                    key_bytes if len(key_bytes) >= 44 else key_bytes + b"=" * (44 - len(key_bytes))
                )
            except Exception:
                logger.error(
                    "❌ ENCRYPTION_KEY en .env no es válida para Fernet (debe ser 32 bytes url-safe base64)."
                )
                if is_production:
                    raise ValueError("ENCRYPTION_KEY inválida en producción.")
        return self

    db_pool_size: int = Field(default=5, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=10, validation_alias="DB_MAX_OVERFLOW")
    db_pool_pre_ping: bool = Field(default=True, validation_alias="DB_POOL_PRE_PING")
    db_pool_recycle: int = Field(default=3600, validation_alias="DB_POOL_RECYCLE")

    # ── Kairos Feature Flags ──
    auto_fix_level: int = Field(default=2, validation_alias="AUTO_FIX_LEVEL")
    context_compression: bool = Field(default=True, validation_alias="CONTEXT_COMPRESSION")
    dsl_stream_agents: bool = Field(
        default=False,
        validation_alias="DSL_STREAM_AGENTS",
        description=(
            "Streaming de los agentes en runs DSL. Default False: el usage del "
            "streaming de DeepInfra no incluye el cache de prompt y el budget "
            "sobrecuenta; el progreso de la GUI sigue por status/mensajes."
        ),
    )
    pytest_timeout_s: int = Field(
        default=30,
        validation_alias="PYTEST_TIMEOUT_S",
        description=(
            "Timeout por test del test_runner (pytest-timeout). Un test colgado "
            "se reporta failed medido en vez de dejar el agregado sin medición."
        ),
    )
    dsl_prompt_cap_tokens: int = Field(
        default=16000,
        validation_alias="DSL_PROMPT_CAP_TOKENS",
        description=(
            "Cap de prompt por llamada en los runs DSL (0 = legado). Con el techo "
            "de contexto del modelo (128k) la compresión disparaba a ~90k y el run "
            "moría antes por presupuesto; el cap mantiene el coste por iteración "
            "acotado (objetivo 75 % del cap)."
        ),
    )
    undercover_mode: bool = Field(default=True, validation_alias="UNDERCOVER_MODE")
    # daemon_mode: SOLO gobierna el heartbeat Kairos
    # (core/bootstrap.py). La aprobación de acciones peligrosas ya NO deriva
    # de este flag — deriva de la PRESENCIA de un host que registre el
    # callback (GUI) — fail-closed si no hay host. Default False: una
    # GUI fresca jamás debe arrancar sin aprobaciones.
    daemon_mode: bool = Field(default=False, validation_alias="DAEMON_MODE")
    self_heal_interval: int = Field(default=120, validation_alias="SELF_HEAL_INTERVAL")
    verbose_logging: bool = Field(default=False, validation_alias="VERBOSE_LOGGING")
    max_subtasks: int = Field(default=8, validation_alias="MAX_SUBTASKS")
    max_agent_iterations: int = Field(default=8, validation_alias="MAX_AGENT_ITERATIONS")
    tools_enabled: bool = Field(default=True, validation_alias="TOOLS_ENABLED")
    allow_code_execution: bool = Field(default=True, validation_alias="ALLOW_CODE_EXECUTION")
    # RLIMIT_AS activo por defecto. El guard VmSize del executor lo omite
    # si la VA del proceso ya excede el cap (límite inútil/dañino ahí).
    sandbox_memory_mb: int = Field(default=512, validation_alias="SANDBOX_MEMORY_MB")
    mcp_max_line_bytes: int = Field(default=4_194_304, validation_alias="MCP_MAX_LINE_BYTES")
    mcp_request_timeout: float = Field(default=120.0, validation_alias="MCP_REQUEST_TIMEOUT")
    mcp_init_timeout: float = Field(default=15.0, validation_alias="MCP_INIT_TIMEOUT")
    embed_normalize: bool = Field(default=True, validation_alias="EMBED_NORMALIZE")
    # vigencia del "Always Allow" de aprobaciones (segundos).
    approval_always_allow_ttl: float = Field(
        default=1800.0, validation_alias="APPROVAL_ALWAYS_ALLOW_TTL"
    )
    # ── Curator de skills (bucle de aprendizaje) ──
    # Opt-in: sin flag no hay pase automático; el walk determinista solo
    # aplica a skills con provenance created_by=agent (bundled intocables).
    skills_curator_enabled: bool = Field(default=False, validation_alias="SKILLS_CURATOR_ENABLED")
    skills_curator_stale_after_days: int = Field(
        default=30, validation_alias="SKILLS_CURATOR_STALE_AFTER_DAYS"
    )
    skills_curator_archive_after_days: int = Field(
        default=60, validation_alias="SKILLS_CURATOR_ARCHIVE_AFTER_DAYS"
    )
    skills_curator_interval_hours: int = Field(
        default=24, validation_alias="SKILLS_CURATOR_INTERVAL_HOURS"
    )
    skills_curator_min_idle_hours: int = Field(
        default=6, validation_alias="SKILLS_CURATOR_MIN_IDLE_HOURS"
    )

    # ── Delegación a subagentes (delegate_task ad-hoc) ──
    delegation_max_concurrent_children: int = Field(
        default=3, validation_alias="DELEGATION_MAX_CONCURRENT_CHILDREN"
    )
    delegation_child_timeout_seconds: float = Field(
        default=600.0, validation_alias="DELEGATION_CHILD_TIMEOUT_SECONDS"
    )
    delegation_orchestrator_enabled: bool = Field(
        default=True, validation_alias="DELEGATION_ORCHESTRATOR_ENABLED"
    )
    delegation_max_spawn_depth: int = Field(
        default=2, validation_alias="DELEGATION_MAX_SPAWN_DEPTH"
    )

    # ── Backends de ejecución (matriz pluggable) ──
    execution_default_backend: str = Field(
        default="local", validation_alias="EXECUTION_DEFAULT_BACKEND"
    )
    execution_docker_image: str = Field(
        default="python:3.12-slim", validation_alias="EXECUTION_DOCKER_IMAGE"
    )
    execution_docker_network: bool = Field(
        default=False, validation_alias="EXECUTION_DOCKER_NETWORK"
    )
    execution_term_grace_seconds: float = Field(
        default=5.0, validation_alias="EXECUTION_TERM_GRACE_SECONDS"
    )
    # Configurabilidad del subsistema de embeddings.
    embed_backend: str = Field(default="local", validation_alias="EMBED_BACKEND")
    embed_ollama_model: str = Field(
        default="nomic-embed-text", validation_alias="EMBED_OLLAMA_MODEL"
    )
    embed_openai_model: str = Field(
        default="text-embedding-3-small", validation_alias="EMBED_OPENAI_MODEL"
    )
    # Endpoint OpenAI-compatible del backend openai de embeddings (p. ej.
    # NVIDIA integrate: https://integrate.api.nvidia.com/v1 — el catálogo
    # embed deja a nvidia/nemotron-3-embed-1b como
    # único modelo invocable con la cuenta del usuario, dim 2048).
    embed_openai_base_url: str = Field(
        default="https://api.openai.com/v1", validation_alias="EMBED_OPENAI_BASE_URL"
    )
    # Clave DEDICADA para embeddings (decisión del usuario: nemotron con su
    # propia clave). Vacía ⇒ retrocompatibilidad con openai_api_key.
    embed_openai_api_key: str = Field(default="", validation_alias="EMBED_OPENAI_API_KEY")
    # Enviar input_type=passage|query al endpoint (nemotron es modelo
    # instruido/asimétrico; OpenAI real rechaza el parámetro) — opt-in.
    embed_openai_input_type: bool = Field(default=False, validation_alias="EMBED_OPENAI_INPUT_TYPE")
    # Segundos que la vía automática espera al indexado antes de seguir sin
    # contexto de código (el indexado continúa en background). 0 = esperar
    # siempre (comportamiento histórico).
    embed_index_timebox: float = Field(default=30.0, validation_alias="EMBED_INDEX_TIMEBOX_S")
    embed_model: str = Field(
        default="intfloat/multilingual-e5-large", validation_alias="EMBED_MODEL"
    )
    embed_device: str = Field(default="auto", validation_alias="EMBED_DEVICE")
    embed_batch_size: int = Field(default=32, validation_alias="EMBED_BATCH_SIZE")
    embed_preload: bool = Field(default=False, validation_alias="EMBED_PRELOAD")
    embed_index_policy: str = Field(default="auto_rebuild", validation_alias="EMBED_INDEX_POLICY")
    auto_provision_workspaces: bool = Field(
        default=False, validation_alias="AUTO_PROVISION_WORKSPACES"
    )
    tool_max_retries: int = Field(default=3, validation_alias="TOOL_MAX_RETRIES")
    tool_backoff_base: float = Field(default=1.5, validation_alias="TOOL_BACKOFF_BASE")
    tool_max_tokens_hard_cap: int = Field(
        default=2_000_000,
        validation_alias="TOOL_MAX_TOKENS_HARD_CAP",
        description=(
            "Techo ABSOLUTO de tokens por workflow. El preset puede declarar un "
            "budget mayor al global (long-horizon) hasta este cap; nunca por "
            "encima. Es la red de la plataforma, no el objetivo del run."
        ),
    )
    tool_max_tokens_per_workflow: int = Field(
        default=80000, validation_alias="TOOL_MAX_TOKENS_PER_WORKFLOW"
    )
    tool_enable_token_budget: bool = Field(
        default=True, validation_alias="TOOL_ENABLE_TOKEN_BUDGET"
    )
    default_workflow: str = Field(default="development", validation_alias="DEFAULT_WORKFLOW")
    hooks_enabled: bool = Field(default=True, validation_alias="HOOKS_ENABLED")
    active_workspace: str = Field(default="main", validation_alias="ACTIVE_WORKSPACE")
    # máximo de sesiones Maestro simultáneas (a demanda).
    maestro_max_sessions: int = Field(default=4, validation_alias="MAESTRO_MAX_SESSIONS")
    tool_calling_global: bool = Field(
        default=True,
        validation_alias="TOOL_CALLING",
        description="Global kill-switch for function calling. False disables tools for ALL roles.",
    )

    # ── LLM Configuration ──
    llm_max_retries: int = Field(default=3, validation_alias="LLM_MAX_RETRIES")
    llm_timeout_seconds: int = Field(
        default=60,
        validation_alias="LLM_TIMEOUT_SECONDS",
        description=(
            "Deprecated: usar LLM_TIMEOUT (consolidado). Se mantiene solo "
            "para compatibilidad con .env antiguos."
        ),
    )
    llm_backoff_factor: float = Field(default=1.5, validation_alias="LLM_BACKOFF_FACTOR")
    # Sesiones multi-run (2-4 workflows concurrentes) consumen ~30 llamadas/min
    # en picos; un techo menor estrangula el despacho y degrada las verificaciones.
    llm_rate_per_minute: int = Field(default=40, validation_alias="LLM_RATE_PER_MINUTE")
    llm_rate_per_hour: int = Field(default=600, validation_alias="LLM_RATE_PER_HOUR")


# Instancia global
settings = Settings()

logger.info("✅ Settings cargados correctamente")


def nvidia_pool_entries() -> dict[str, str]:
    """Entradas del pool NVIDIA (``NVIDIA_API_KEY_<n>``, ``NVIDIA_MODEL_<n>``).

    Fuente DUAL: os.environ y el fichero .env — pydantic-settings lee el
    fichero para los CAMPOS declarados pero NO lo exporta a os.environ, así
    que un pool declarado solo en .env quedaría invisible para el resolutor
    (la clave estaría en .env y el proveedor caería a Ollama). os.environ
    manda sobre el fichero (paridad con pydantic-settings).
    """
    import os

    entradas: dict[str, str] = {}
    env_files: str | Path | list[str | Path] = Settings.model_config.get(
        "env_file", ".env"
    )  # type: ignore[assignment]
    ficheros: list[str | Path] = (
        [env_files] if isinstance(env_files, (str, Path)) else list(env_files or [])
    )
    for fichero in ficheros:
        f = Path(fichero)
        if not f.is_file():
            continue
        for linea in f.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            clave = clave.strip()
            if clave.startswith("NVIDIA_"):
                entradas[clave] = valor.strip().strip('"').strip("'")
    for clave, valor in os.environ.items():  # os.environ manda
        if clave.startswith("NVIDIA_"):
            entradas[clave] = valor
    return entradas
