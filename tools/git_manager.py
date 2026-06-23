import asyncio
import logging

from agents.audit import log_operation
from core.path_resolver import paths

logger = logging.getLogger(__name__)


class GitManager:
    @staticmethod
    async def execute(
        action: str,
        workspace: str = "main",
        file: str = "",
        message: str = "commit automático",
        project_root: str | None = None,
        **kwargs,
    ) -> str:
        base = paths.memory_dir(workspace)

        # Determinar el directorio del proyecto
        if project_root:
            project_root = paths.normalize_project_root(project_root)
            repo_path = (base / project_root).resolve()  # type: ignore[operator]  # type: ignore[operator]
        else:
            # Si no se especifica, no adivinamos; devolvemos error
            return "❌ Error: git_manager necesita 'project_root' (ej. 'code_projects/miapp')"

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
            if not (repo_path / ".git").exists():
                repo_path.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(Repo.init, repo_path)
                return f"Repositorio Git inicializado en {project_root}."
            return "Repositorio ya existente."

        # For all other actions, the repository must exist
        if not (repo_path / ".git").exists():
            return "❌ No hay un repositorio Git inicializado en este proyecto."

        try:
            from git import Repo
        except ImportError:
            return "❌ GitPython no está instalado. Ejecuta: pip install gitpython"
        repo = Repo(repo_path)

        if action == "add":
            await asyncio.to_thread(repo.index.add, ["*"])
            log_operation("git_add", str(repo_path), success=True)
            return "Archivos añadidos al área de staging."
        elif action == "commit":
            if not message or message.startswith("❌") or "rate limit" in message.lower():
                return "❌ Mensaje de commit no válido: parece una respuesta de error del sistema."
            await asyncio.to_thread(repo.index.commit, message)
            log_operation("git_commit", f"{repo_path}: {message[:100]}", success=True)
            return f"Commit realizado: {message}"
        elif action == "log":
            commits = await asyncio.to_thread(lambda: list(repo.iter_commits(max_count=5)))
            return "\n".join([f"{c.hexsha[:7]} - {c.message} ({c.author})" for c in commits])  # type: ignore[str-bytes-safe]
        elif action == "diff":
            diff = (
                await asyncio.to_thread(repo.git.diff, file)
                if file
                else await asyncio.to_thread(repo.git.diff)
            )
            return diff if diff else "Sin cambios."
        else:
            raise ValueError(f"Acción '{action}' no soportada.")

    @staticmethod
    async def remote(
        action: str = "status",
        workspace: str = "main",
        file: str = "",
        message: str = "commit automático",
        project_root: str | None = None,
        **kwargs,
    ) -> str:
        base = paths.memory_dir(workspace)

        # Determinar el directorio del proyecto
        if project_root:
            project_root = paths.normalize_project_root(project_root)
            repo_path = (base / project_root).resolve()  # type: ignore[operator]
        else:
            # Si no se especifica, no adivinamos; devolvemos error
            return "❌ Error: git_manager necesita 'project_root' (ej. 'code_projects/miapp')"

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
            if not (repo_path / ".git").exists():
                repo_path.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(Repo.init, repo_path)
                return f"Repositorio Git inicializado en {project_root}."
            return "Repositorio ya existente."

        # For all other actions, the repository must exist
        if not (repo_path / ".git").exists():
            return "❌ No hay un repositorio Git inicializado en este proyecto."

        try:
            from git import Repo
        except ImportError:
            return "❌ GitPython no está instalado. Ejecuta: pip install gitpython"
        repo = Repo(repo_path)

        if action == "add":
            await asyncio.to_thread(repo.index.add, ["*"])
            log_operation("git_add", str(repo_path), success=True)
            return "Archivos añadidos al área de staging."
        elif action == "commit":
            if not message or message.startswith("❌") or "rate limit" in message.lower():
                return "❌ Mensaje de commit no válido: parece una respuesta de error del sistema."
            await asyncio.to_thread(repo.index.commit, message)
            log_operation("git_commit", f"{repo_path}: {message[:100]}", success=True)
            return f"Commit realizado: {message}"
        elif action == "log":
            commits = await asyncio.to_thread(lambda: list(repo.iter_commits(max_count=5)))
            return "\n".join([f"{c.hexsha[:7]} - {c.message} ({c.author})" for c in commits])  # type: ignore[str-bytes-safe]
        elif action == "diff":
            diff = (
                await asyncio.to_thread(repo.git.diff, file)
                if file
                else await asyncio.to_thread(repo.git.diff)
            )
            return diff if diff else "Sin cambios."
        else:
            raise ValueError(f"Acción '{action}' no soportada.")


from tools.registry import tools_registry


@tools_registry.register("git_manager")
async def git_manager_tool(
    action: str = "status",
    workspace: str = "main",
    file: str = "",
    message: str = "commit automático",
    project_root: str | None = None,
    **kwargs,
) -> str:
    if not action:
        return (
            "❌ git_manager requiere un parámetro 'action' (init, add, commit, log, diff, status)"
        )
    if not project_root:
        logger.debug(
            f"git_manager llamada sin project_root. action='{action}', kwargs={list(kwargs.keys())}"
        )
        return (
            "❌ git_manager necesita 'project_root' (ej: 'code_projects/miapp'). "
            "Especifica el directorio del proyecto donde existe el repositorio Git."
        )
    return await GitManager.execute(action, workspace, file, message, project_root=project_root)
