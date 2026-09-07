"""FileViewerService — abre archivos en el visor standalone (Qt-free).

Spawn de proceso (patrón dashboard_service): el visor es un binario
externo lógico — cero acoplamiento de código con Morphix.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys

from core.path_resolver import paths

logger = logging.getLogger(__name__)


def has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_in_viewer(path: str | os.PathLike) -> bool:
    """Abre `path` en viewer/viewer.py como proceso independiente.

    True si el spawn se lanzó; False con log si no (headless, script
    ausente, error de spawn). Nunca lanza hacia la GUI.
    """
    try:
        p = paths.project_root() / path if not os.path.isabs(str(path)) else path
        script = paths.viewer_script()
        if not script.is_file():
            logger.error("Viewer no encontrado: %s", script)
            return False
        if not has_display():
            logger.warning("Sin display — no se abre el visor para %s", path)
            return False
        if platform.system() == "Windows":
            args = [sys.executable, str(script), str(p)]
        else:
            args = [sys.executable, str(script), str(p)]
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError as e:
        logger.error("No se pudo abrir el visor para %s: %s", path, e)
        return False
