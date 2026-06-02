"""Bootstrap — inicialización del backend para el modo desktop PySide6."""

import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

logger = logging.getLogger(__name__)
_daemon_tasks: list[asyncio.Task] = []

from collections.abc import Callable
from typing import Any


def validate_config() -> tuple[bool, list[str]]:
    """Validate critical configuration at startup.

    Returns (valid: bool, warnings: list[str]).
    Fatal errors raise ValueError. Warnings are non-blocking.
    """
    from core.config import settings

    errors: list[str] = []
    warnings: list[str] = []

    # DATABASE_URL is required
    if not settings.database_url:
        errors.append("DATABASE_URL is not set. Copy example.env to .env and configure it.")

    # At least one API key (unless offline mode)
    if not settings.offline_mode:
        has_key = any(
            [
                settings.openai_api_key,
                settings.deepseek_api_key,
                settings.grok_api_key,
            ]
        )
        if not has_key:
            warnings.append(
                "No API keys configured (OPENAI_API_KEY, DEEPSEEK_API_KEY, or GROK_API_KEY). "
                "Enable OFFLINE_MODE=true to use Ollama locally."
            )

    # ENCRYPTION_KEY in production
    from core.config import os as _os

    morphix_env = _os.getenv("MORPHIX_ENV", "development")
    if morphix_env == "production" and not settings.encryption_key:
        errors.append("ENCRYPTION_KEY is required in production (MORPHIX_ENV=production).")

    # Critical directory existence
    from core.path_resolver import MEMORY_BASE, TEMPLATES_DIR

    if not MEMORY_BASE.exists():
        warnings.append(f"Memory base directory missing: {MEMORY_BASE}")

    if not TEMPLATES_DIR.exists():
        warnings.append(f"Templates directory missing: {TEMPLATES_DIR}")

    if errors:
        raise ValueError("Configuration errors:\n- " + "\n- ".join(errors))

    return True, warnings


async def init_backend(
    workspace: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> bool:
    """Inicializa BD, workspace, y agentes."""
    if workspace is None:
        from core.config import settings

        workspace = settings.active_workspace
    try:
        from core.database import startup_db

        if on_progress:
            on_progress("Conectando a base de datos...")
        await startup_db()
        logger.info("Base de datos inicializada")
    except Exception as e:
        logger.critical(f"Error initializing DB: {e}")
        if on_progress:
            on_progress(f"Error BD: {e}")
        return False

    try:
        from core.workspaces import get_global_workspaces

        if on_progress:
            on_progress(f"Activando workspace '{workspace}'...")
        ws = get_global_workspaces()
        await ws.switch_workspace(workspace)
        logger.info(f"Workspace '{workspace}' activado")
    except Exception as e:
        logger.critical(f"Error activando workspace: {e}")
        if on_progress:
            on_progress(f"Error workspace: {e}")
        return False

    try:
        from core.hook_loader import load_global_hooks

        load_global_hooks()
        logger.info("Global hooks loaded")
    except Exception as e:
        logger.warning(f"Error loading global hooks: {e}")

    return True


async def start_daemons(on_offline_changed: Callable[[bool], Any] | None = None) -> None:
    """Arranca tareas de fondo: Kairos Daemon y OfflineManager.
    on_offline_changed: callback opcional async para notificar cambios de estado offline.
    """
    global _daemon_tasks

    from core.config import settings
    from core.feature_flags import kairos

    if settings.daemon_mode:
        logger.info("✅ Kairos Daemon Mode activado")
        _daemon_tasks.append(asyncio.create_task(kairos.daemon_loop()))

    from llm import OfflineManager

    om = OfflineManager()
    await om.detect()

    async def _periodic_offline_check():
        was_offline = om.is_offline()
        while True:
            try:
                await asyncio.sleep(300)
                await om.detect()
                is_offline = om.is_offline()
                if is_offline != was_offline:
                    logger.info(f"Offline status changed: {is_offline}")
                    was_offline = is_offline
                    if on_offline_changed is not None:
                        await on_offline_changed(is_offline)
            except asyncio.CancelledError:
                break

    _daemon_tasks.append(asyncio.create_task(_periodic_offline_check()))
    logger.info("OfflineManager iniciado")


async def stop_daemons() -> None:
    """Cancela todas las tareas de fondo limpiamente."""
    global _daemon_tasks
    for task in _daemon_tasks:
        task.cancel()
    if _daemon_tasks:
        await asyncio.gather(*_daemon_tasks, return_exceptions=True)
    _daemon_tasks.clear()
    logger.info("Daemons detenidos")
