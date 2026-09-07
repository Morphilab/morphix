# desktop/services/config_service.py
import logging

from core.config import settings
from llm import OfflineManager

logger = logging.getLogger(__name__)

offline_manager = OfflineManager()


class ConfigService:
    """Servicio centralizado para la lógica de configuración."""

    @staticmethod
    def toggle_offline_mode():
        """Activa/desactiva modo offline (toggle)."""
        offline_manager.toggle_offline()
        logger.info(f"ConfigService: toggle_offline_mode → {settings.offline_mode}")
        return {"success": True, "offline_mode": settings.offline_mode}
