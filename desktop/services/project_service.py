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


def clone_project(url: str, name: str | None = None) -> tuple[bool, str]:
    """Clona un repo git → code_projects/<name>. Retorna (ok, message).

    - Rechaza si el destino ya existe (sin invocar git).
    - `--depth 1` + timeout duro 120s.
    - Limpia el clon parcial ante cualquier fallo.
    - Redacta credenciales del mensaje de error (URLs con user:token)."""
    import shutil
    import subprocess

    from agents.audit import redact_credentials

    if name is None:
        # derivar nombre del repo desde la URL
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        tail = tail[:-4] if tail.endswith(".git") else tail
        name = normalize_project_name(tail)
    else:
        name = normalize_project_name(name)
    if not name:
        return False, "Nombre de proyecto inválido. Usa solo letras, números y _"

    dst = project_dir(name)
    if dst.exists():
        return False, f"Ya existe un proyecto llamado '{name}'"

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dst)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(dst, ignore_errors=True)
        return False, "git clone excedió el timeout (120s)"
    except FileNotFoundError:
        return False, "git no está disponible en el sistema"

    if result.returncode != 0:
        shutil.rmtree(dst, ignore_errors=True)
        err_text = (result.stderr or result.stdout or "git clone falló").strip()
        last_line = err_text.splitlines()[-1] if err_text.splitlines() else "git clone falló"
        logger.error(f"Error clonando {redact_credentials(url)}: {last_line}")
        return False, redact_credentials(last_line)

    file_count = sum(1 for _ in dst.rglob("*") if _.is_file())
    return True, f"{name} ({file_count} archivos)"
