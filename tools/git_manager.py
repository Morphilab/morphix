import asyncio
import logging
import os
import threading
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, TypeVar

from agents.audit import log_operation
from core.config import settings
from core.constants import PROJECTS_DIR_NAME, SECRET_EXCLUDE_PATHSPEC
from core.path_resolver import paths
from core.utils import build_child_env

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# locks por project root — serializa las operaciones de git
# que mutan el índice (init/add/commit) para que dos workflows sobre el mismo
# proyecto no intercalen sus commits. Keyed por repo path resuelto.
_repo_locks: dict[str, asyncio.Lock] = {}
_repo_locks_guard = threading.Lock()


def get_repo_lock(repo_key: str) -> asyncio.Lock:
    """Lock por project root (mismo path → mismo lock; paths distintos aislados)."""
    with _repo_locks_guard:
        lock = _repo_locks.get(repo_key)
        if lock is None:
            lock = asyncio.Lock()
            _repo_locks[repo_key] = lock
        return lock


@contextmanager
def _git_env_guard():
    """GitPython spawnea `git` heredando os.environ completo — build_child_env()
    solo cubre bash/MCP. Swap in-place al entorno purgado
    durante la operación y restauración garantizada en finally. Nota de concurrencia:
    si otro hilo spawnea un hijo durante la ventana, ve el entorno PURGADO (dirección
    segura); nunca lo contrario."""
    snapshot = dict(os.environ)
    clean = build_child_env()
    try:
        os.environ.clear()
        os.environ.update(clean)
        yield
    finally:
        os.environ.clear()
        os.environ.update(snapshot)


def _scrubbed(fn: F) -> Callable[..., Any]:
    def run(*args: Any, **kwargs: Any) -> Any:
        with _git_env_guard():
            return fn(*args, **kwargs)

    return run


class GitManager:
    @staticmethod
    async def execute(
        action: str,
        workspace: str | None = None,
        file: str = "",
        message: str = "commit automático",
        project_root: str | None = None,
        **kwargs,
    ) -> "str | dict[str, object]":
        if not (action or "").strip():
            return (
                "❌ git_manager requiere un parámetro 'action' "
                "(init, add, commit, log, diff, show)"
            )
        if workspace is None:
            workspace = settings.active_workspace
        base = paths.memory_dir(workspace)

        # Determinar el directorio del proyecto
        if project_root:
            project_root = paths.normalize_project_root(project_root)
            repo_path = (base / project_root).resolve()  # type: ignore[operator]
        else:
            # Si no se especifica, no adivinamos; devolvemos error
            return (
                f"❌ Error: git_manager necesita 'project_root' (ej. '{PROJECTS_DIR_NAME}/miapp')"
            )

        # Security: don't leave the workspace
        try:
            repo_path.relative_to(base.resolve())
        except ValueError:
            raise ValueError("Ruta fuera del workspace")

        if action == "init":
            try:
                from git import Repo
            except ImportError:
                return "❌ GitPython no está instalado. Ejecuta: pip install gitpython"
            async with get_repo_lock(str(repo_path)):
                if not (repo_path / ".git").exists():
                    repo_path.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(_scrubbed(Repo.init), repo_path)
                    return f"Repositorio Git inicializado en {project_root}."
                return "Repositorio ya existente."

        # For all other actions, the repository must exist
        if not (repo_path / ".git").exists():
            return "❌ No hay un repositorio Git inicializado en este proyecto."

        try:
            from git import Repo
        except ImportError:
            return "❌ GitPython no está instalado. Ejecuta: pip install gitpython"
        # Repo.__init__ también spawnea git (lectura de config/version)
        repo: Any = await asyncio.to_thread(_scrubbed(Repo), repo_path)

        if action == "add":
            # excludes de secretos POR DEFECTO — add sin excludes nunca
            # staguea .env/.pem/.key/secrets/**. Excludes explícitos los reemplazan.
            excludes = kwargs.get("excludes") or list(SECRET_EXCLUDE_PATHSPEC)
            # pathspecs de exclusión (secretos) vía CLI git — semántica estándar
            async with get_repo_lock(str(repo_path)):
                await asyncio.to_thread(_scrubbed(repo.git.add), "-A", "--", ".", *excludes)
            log_operation("git_add", str(repo_path), success=True)
            return "Archivos añadidos al área de staging."
        elif action == "commit":
            if not message or message.startswith("❌") or "rate limit" in message.lower():
                return "❌ Mensaje de commit no válido: parece una respuesta de error del sistema."
            async with get_repo_lock(str(repo_path)):
                await asyncio.to_thread(_scrubbed(repo.index.commit), message)
            log_operation("git_commit", f"{repo_path}: {message[:100]}", success=True)
            # '': retorno estructurado — callers deciden por 'success', no substring
            return {"success": True, "output": f"Commit realizado: {message}"}
        elif action == "log":

            def _fmt_log() -> "list[str]":
                # Formatear DENTRO del guard: iter_commits es perezoso y los accesos
                # a .hexsha/.message/.author pueden disparar cat-file post-list().
                return [
                    f"{c.hexsha[:7]} - {c.message} ({c.author})"
                    for c in repo.iter_commits(max_count=5)
                ]

            commits = await asyncio.to_thread(_scrubbed(_fmt_log))
            return "\n".join(commits)  # type: ignore[str-bytes-safe]
        elif action == "diff":
            diff = (
                await asyncio.to_thread(_scrubbed(repo.git.diff), "--", file)
                if file
                else await asyncio.to_thread(_scrubbed(repo.git.diff))
            )
            return diff if diff else "Sin cambios."
        elif action == "show":
            # Task 2.1 doc 4: read-only — permite verificar contenido histórico
            # (ej. rama pública) sin fricción de aprobación.
            ref_path = kwargs.get("ref_path") or file
            if not ref_path:
                return "❌ La acción 'show' requiere 'ref_path' (ej. 'HEAD:README.md' o 'public:docs/x.md')."
            try:
                content = await asyncio.to_thread(_scrubbed(repo.git.show), ref_path)
            except Exception as e:
                return f"❌ git show '{ref_path}' falló: {e}"
            return str(content)
        else:
            raise ValueError(f"Acción '{action}' no soportada.")


from tools.registry import tools_registry


@tools_registry.register("git_manager")
async def git_manager_tool(
    action: str = "",
    workspace: str | None = None,
    file: str = "",
    message: str = "commit automático",
    project_root: str | None = None,
    **kwargs,
) -> "str | dict[str, object]":
    if workspace is None:
        workspace = settings.active_workspace
    if not action:
        return "❌ git_manager requiere un parámetro 'action' (init, add, commit, log, diff, show)"
    if not project_root:
        logger.debug(
            f"git_manager llamada sin project_root. action='{action}', kwargs={list(kwargs.keys())}"
        )
        return (
            f"❌ git_manager necesita 'project_root' (ej: '{PROJECTS_DIR_NAME}/miapp'). "
            "Especifica el directorio del proyecto donde existe el repositorio Git."
        )
    return await GitManager.execute(action, workspace, file, message, project_root=project_root)
