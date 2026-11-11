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


def project_stats(name: str, workspace: str | None = None) -> tuple[int, int] | None:
    """(n_archivos, bytes) del proyecto, o None si no existe.

    Para el diálogo de confirmación de borrado: el usuario debe ver el
    tamaño de lo que va a perder antes de confirmar.
    """
    proj = project_dir(name, workspace)
    if not proj.is_dir():
        return None
    files = [f for f in proj.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


def delete_project(name: str, workspace: str | None = None) -> tuple[bool, str]:
    """Borra el directorio de un proyecto. Retorna (ok, message).

    Seguridad (en orden):
    - nombre normalizado e invalidado (anti path-traversal: el nombre se
      valida como identificador, igual que al crear);
    - el destino resuelto debe quedar DENTRO de projects_base();
    - rechaza con un run activo (workflow_running): aunque el run use otro
      proyecto, la regla es simple y sin carreras.

    El borrado es de DIRECTORIO (filesystem): la BD no referencia proyectos
    por ruta (workflow guarda `query`, no project_root).
    """
    import shutil

    from core.workspaces import workflow_running

    name = normalize_project_name(name) or ""
    if not name:
        return False, "Nombre de proyecto inválido. Usa solo letras, números y _"

    if workflow_running():
        return False, "Hay un flujo activo — detén la ejecución antes de borrar un proyecto"

    proj = project_dir(name, workspace).resolve()
    base = projects_base(workspace).resolve()
    try:
        proj.relative_to(base)
    except ValueError:
        logger.warning("Borrado rechazado fuera de la base de proyectos: %s", proj)
        return False, "Ruta fuera del área de proyectos"

    if not proj.is_dir():
        return False, f"No existe un proyecto llamado '{name}'"

    try:
        shutil.rmtree(proj)
    except Exception as e:  # noqa: BLE001 — el mensaje al usuario manda
        logger.error("Error borrando proyecto '%s': %s", name, e, exc_info=True)
        return False, str(e)
    logger.info("Proyecto borrado: %s", name)
    return True, name
