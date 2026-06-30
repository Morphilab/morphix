# features/config/services/config_service.py
import logging
import os
import platform
import subprocess
import sys
import threading

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

    @staticmethod
    def restart_application():
        """Reinicia la aplicación de forma segura."""
        try:
            logging.info("Iniciando reinicio de aplicación...")
            python = os.sys.executable
            script = "run.py"

            if platform.system() == "Windows":
                subprocess.Popen([python, script], creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                subprocess.Popen(
                    ["nohup", python, script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                    preexec_fn=os.setpgrp,
                )

            threading.Timer(1.0, lambda: sys.exit(0)).start()
            return {"success": True}
        except Exception as e:
            logging.error(f"Error en restart: {e}")
            return {"success": False, "message": str(e)}

    @staticmethod
    def toggle_dark_mode(enabled: bool):
        """Cambia el tema dark/light."""
        settings.dark_mode = enabled
        return {"success": True}
