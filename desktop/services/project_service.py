"""ProjectService — gestión de proyectos (crear/importar/precargar) sin lógica de UI."""

from __future__ import annotations

import logging
from pathlib import Path

from core.constants import PROJECTS_DIR_NAME
from core.path_resolver import paths

logger = logging.getLogger(__name__)


def active_workspace() -> str:
    from core.workspaces import get_global_workspaces

    return get_global_workspaces().current


def projects_base(workspace: str | None = None) -> Path:
    """Directorio base de proyectos del workspace (memory/<ws>/code_projects)."""
    ws = workspace or active_workspace()
    return paths.memory_dir(ws) / PROJECTS_DIR_NAME


def project_dir(name: str, workspace: str | None = None) -> Path:
    return projects_base(workspace) / name


def normalize_project_name(raw: str) -> str | None:
    name = raw.strip().lower().replace(" ", "_")
    if not name or not name.isidentifier():
        return None
    return name


def create_project(name: str) -> tuple[bool, str]:
    """Crea el directorio del proyecto. Retorna (ok, root_rel)."""
    proj_dir = project_dir(name)
    proj_dir.mkdir(parents=True, exist_ok=True)
    return True, f"{PROJECTS_DIR_NAME}/{name}"


def import_project(src: str, name: str) -> tuple[bool, str]:
    """Copia src → code_projects/name. Retorna (ok, message)."""
    import shutil

    dst = project_dir(name)
    if dst.exists():
        return False, f"Ya existe un proyecto llamado '{name}'"
    try:
        shutil.copytree(src, str(dst))
        file_count = sum(1 for _ in dst.rglob("*") if _.is_file())
        return True, f"{name} ({file_count} archivos)"
    except Exception as e:
        logger.error(f"Error copiando proyecto: {e}", exc_info=True)
        return False, str(e)
