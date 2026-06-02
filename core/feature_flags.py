# core/feature_flags.py
"""
Kairos Feature Flags + Daemon Mode (Claude Code Style - Marzo 2026)
"""

import asyncio
import logging
import os
import threading
import time
from typing import Any

from core.memory.manager import memory

logger = logging.getLogger(__name__)


class KairosFlags:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_flags()
        return cls._instance

    def _init_flags(self):
        from core.config import settings as app_settings

        self.flags: dict[str, Any] = {
            "AUTO_FIX_LEVEL": app_settings.auto_fix_level,
            "CONTEXT_COMPRESSION": app_settings.context_compression,
            "UNDERCOVER_MODE": app_settings.undercover_mode,
            "DAEMON_MODE": app_settings.daemon_mode,
            "SELF_HEAL_INTERVAL": app_settings.self_heal_interval,
            "VERBOSE_LOGGING": app_settings.verbose_logging,
            "MAX_SUBTASKS": app_settings.max_subtasks,
            "tools_enabled": app_settings.tools_enabled,
            "allow_code_execution": app_settings.allow_code_execution,
            "tool_max_retries": app_settings.tool_max_retries,
            "tool_backoff_base": app_settings.tool_backoff_base,
            "tool_max_tokens_per_workflow": app_settings.tool_max_tokens_per_workflow,
            "tool_enable_token_budget": app_settings.tool_enable_token_budget,
            "AGENT_SELF_REFLECTION": app_settings.agent_self_reflection,
            "HOOKS_ENABLED": app_settings.hooks_enabled,
        }
        # Flags que se han modificado en caliente (no recargar del .env)
        self._dirty_flags: set[str] = set()
        logger.info("🚀 Kairos Feature Flags inicializados")
        logger.info(f"   DAEMON_MODE: {self.flags['DAEMON_MODE']}")
        logger.info(f"   TOOLS_ENABLED: {self.flags['tools_enabled']}")
        logger.info(f"   ALLOW_CODE_EXECUTION: {self.flags['allow_code_execution']}")
        logger.info(
            f"   TOOL_MAX_TOKENS_PER_WORKFLOW: {self.flags['tool_max_tokens_per_workflow']}"
        )

    def get(self, key: str, default: Any = None) -> Any:
        """Obtener flag. Solo recarga del .env si no fue modificado en caliente."""
        # If the flag was already changed manually, don't overwrite from the environment
        if key not in self._dirty_flags:
            env_value = os.getenv(key)
            if env_value is not None:
                if isinstance(self.flags.get(key), bool):
                    self.flags[key] = env_value.lower() == "true"
                elif isinstance(self.flags.get(key), int):
                    self.flags[key] = int(env_value)
                elif isinstance(self.flags.get(key), float):
                    self.flags[key] = float(env_value)
                else:
                    self.flags[key] = env_value
        return self.flags.get(key, default)

    def set(self, key: str, value: Any):
        """Cambiar flag en runtime (marcado como manual para evitar hot-reload)."""
        self.flags[key] = value
        self._dirty_flags.add(key)
        logger.info(f"🔄 Flag actualizado: {key} = {value}")

    async def daemon_loop(self):
        """Modo Daemon siempre activo"""
        logger.info("🔄 Kairos Daemon iniciado - Modo siempre activo")
        try:
            while True:
                try:
                    if self.get("DAEMON_MODE"):

                        await memory.write_system(
                            "kairos_daemon_heartbeat",
                            {
                                "timestamp": time.time(),
                                "flags_active": len([k for k, v in self.flags.items() if v]),
                                "auto_fix_level": self.get("AUTO_FIX_LEVEL"),
                            },
                        )

                        await memory.self_healing_check()
                        logger.debug("💓 Daemon heartbeat enviado")
                    await asyncio.sleep(self.get("SELF_HEAL_INTERVAL"))
                except Exception as e:
                    logger.error("Error en daemon loop: %s", e, exc_info=True)
                    await asyncio.sleep(10)
        except asyncio.CancelledError:
            logger.info("Kairos Daemon cancelado")


# Instancia global
kairos = KairosFlags()
