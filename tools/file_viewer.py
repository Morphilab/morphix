# tools/file_viewer.py
"""file_view — abre un archivo en el visor standalone (fire-and-forget).

"muéstrame X" NO devuelve el contenido al agente (para eso están
pdf_read/file_manager). Spawn de proceso con viewer/viewer.py; errores →
string accionable. Headless (sin display) no spawnea.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys

from core.config import settings
from core.path_resolver import paths

logger = logging.getLogger(__name__)


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


async def _file_view_tool(
    path: str = "",
    workspace: str | None = None,
    project_root: str | None = None,
    **_: object,
) -> str:
    if not path.strip():
        return "❌ file_view requiere el parámetro 'path' (relativo al proyecto)."
    if not _has_display():
        return "Sin display — usa pdf_read/file_manager para leer el contenido."

    if workspace is None:
        workspace = settings.active_workspace
    base = paths.code_projects_dir(workspace, project_root).resolve()

    resolved = (base / path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        return f"❌ Acceso denegado: {path} está fuera del workspace."

    if not resolved.is_file():
        return f"❌ Archivo no encontrado: {resolved}"

    script = paths.viewer_script()
    if not script.is_file():
        return f"❌ Visor no encontrado en {script} (instalación incompleta)."

    def _spawn() -> None:
        subprocess.Popen(
            [sys.executable, str(script), str(resolved)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    try:
        await asyncio.to_thread(_spawn)
    except OSError as e:
        logger.warning("No se pudo abrir el visor: %s", e)
        return f"❌ No se pudo abrir el visor: {e}"
    return f"Abierto en el visor: {resolved.name}"


def register(registry) -> None:
    registry.register("file_view")(_file_view_tool)


# Auto-registro (mismo patrón que memory_inspector/skill_loader)
from tools.registry import tools_registry  # noqa: E402

register(tools_registry)  # noqa: E402
