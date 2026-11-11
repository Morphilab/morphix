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

    @staticmethod
    def set_dark_mode(enabled: bool) -> dict:
        """Cambia el modo de la GUI (True = oscuro papel, False = papel claro) y
        lo persiste en .env para que sobreviva al reinicio — el tema se resuelve
        al arrancar (aplicación efectiva tras reiniciar)."""
        settings.dark_mode = bool(enabled)
        value = "true" if enabled else "false"
        try:
            from core.config import ENV_PATH

            lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
            written = False
            out = []
            for line in lines:
                if line.strip().startswith("DARK_MODE"):
                    out.append(f"DARK_MODE={value}")
                    written = True
                else:
                    out.append(line)
            if not written:
                out.append(f"DARK_MODE={value}")
            tmp = ENV_PATH.with_name(".env.tmp")
            tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
            tmp.replace(ENV_PATH)
        except Exception as e:  # persistencia best-effort — no rompe el toggle en sesión
            logger.warning("No se pudo persistir DARK_MODE en .env: %s", e)
        logger.info(f"ConfigService: set_dark_mode → {settings.dark_mode}")
        return {"success": True, "dark_mode": settings.dark_mode}
