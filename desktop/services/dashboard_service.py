# features/dashboard/services/dashboard_service.py
import logging
import os
import platform
import subprocess

from core.path_resolver import paths

logger = logging.getLogger(__name__)


class DashboardService:
    """Servicio para la lógica de negocio del Dashboard."""

    @staticmethod
    def open_logs() -> dict:
        """Abre el archivo de logs con el visor por defecto del sistema."""
        try:
            log_path = str(paths.log_file())
            if platform.system() == "Windows":
                os.startfile(log_path)  # type: ignore[attr-defined]
            elif platform.system() == "Linux":
                subprocess.Popen(
                    ["xdg-open", log_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    ["open", log_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return {"success": True}
        except Exception as e:
            logger.error("Error abriendo logs: %s", e)
            return {"success": False, "message": str(e)}

    @staticmethod
    def open_logs_lnav() -> dict:
        """Abre el archivo de logs con lnav (visor de logs en tiempo real)."""
        log_path = str(paths.log_file())
        if not os.path.exists(log_path):
            return {"success": False, "message": "Archivo de log no encontrado"}

        try:
            if platform.system() == "Linux":
                try:
                    subprocess.Popen(
                        ["x-terminal-emulator", "-e", f"lnav {log_path}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except FileNotFoundError:
                    subprocess.Popen(
                        ["gnome-terminal", "--", "lnav", log_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            elif platform.system() == "Windows":
                subprocess.Popen(
                    ["cmd", "/c", "start", "lnav", log_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    ["open", log_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return {"success": True}
        except FileNotFoundError:
            return {"success": False, "message": "lnav no encontrado"}
        except Exception as e:
            logger.error("Error abriendo logs con lnav: %s", e)
            return {"success": False, "message": str(e)}
