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
        default=True, validation_alias="DARK_MODE", description="Modo oscuro por defecto"
    )
    offline_mode: bool = Field(default=False, validation_alias="OFFLINE_MODE")

    # API Keys
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    grok_api_key: str = Field(default="", validation_alias="GROK_API_KEY")
    google_api_key: str = Field(default="", validation_alias="GOOGLE_API_KEY")
    google_cx: str = Field(default="", validation_alias="GOOGLE_CX")
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
    deepseek_strict_mode: bool = Field(
        default=False,
        validation_alias="DEEPSEEK_STRICT_MODE",
        description="Activar strict mode de DeepSeek para forzar respeto de required en tools",
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
            "default": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "temperature": 0.7,
                "max_tokens": 4096,
                "tool_calling": True,
            },
            "fast": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "temperature": 0.3,
                # Mínimo 1024: con 512 el reasoning agota el presupuesto y la
                # respuesta llega vacía (starvation).
                # El reasoning se contiene vía retry B3 y no reenviando
                # reasoning_content.
                "max_tokens": 1024,
                "tool_calling": True,
            },
            "reasoning": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "temperature": 0.0,
                "max_tokens": 4096,
                "tool_calling": True,
            },
            "agent": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "temperature": 0.7,
                "max_tokens": 4096,
                "tool_calling": True,
            },
            "creative": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "temperature": 0.9,
                "max_tokens": 4096,
                "tool_calling": True,
            },
            "critique": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "temperature": 0.0,
                "max_tokens": 1024,
                "tool_calling": True,
            },
            # Visión: análisis one-shot de imágenes
            # vía tool vision_analyze. tool_calling False: sin tools; los
            # modelos -exp pueden ir detrás en texto/tool-calling y así el
            # rol nunca sustituye al default.
            "vision": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash-vision-exp",
                "ollama_model": "qwen2.5vl:7b",
                "temperature": 0.3,
                "max_tokens": 2048,
                "tool_calling": False,
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
    # Configurabilidad del subsistema de embeddings.
    embed_backend: str = Field(default="local", validation_alias="EMBED_BACKEND")
    embed_ollama_model: str = Field(
        default="nomic-embed-text", validation_alias="EMBED_OLLAMA_MODEL"
    )
    embed_openai_model: str = Field(
        default="text-embedding-3-small", validation_alias="EMBED_OPENAI_MODEL"
    )
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
    tool_max_tokens_per_workflow: int = Field(
        default=80000, validation_alias="TOOL_MAX_TOKENS_PER_WORKFLOW"
    )
    tool_enable_token_budget: bool = Field(
        default=True, validation_alias="TOOL_ENABLE_TOKEN_BUDGET"
    )
    agent_self_reflection: bool = Field(default=False, validation_alias="AGENT_SELF_REFLECTION")
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
    llm_rate_per_minute: int = Field(default=20, validation_alias="LLM_RATE_PER_MINUTE")
    llm_rate_per_hour: int = Field(default=200, validation_alias="LLM_RATE_PER_HOUR")


# Instancia global
settings = Settings()

logger.info("✅ Settings cargados correctamente")
