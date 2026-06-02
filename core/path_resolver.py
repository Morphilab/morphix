# core/path_resolver.py
"""
PathResolver — resolución centralizada de rutas del sistema.
Elimina hardcodeos de Path("memory"), Path("workspaces"), Path("graficos"), etc.
"""
from pathlib import Path

_BASE = Path(__file__).parent.parent

# Project root paths (CWD-independent)
MEMORY_BASE = _BASE / "memory"
WORKSPACES_BASE = _BASE / "workspaces"
TEMPLATES_DIR = _BASE / "templates"
CHARTS_DIR = _BASE / "charts"
SESSIONS_DIR = _BASE / "sessions"
EXPORTS_DIR = _BASE / "exports"
DATA_DIR = _BASE / "data"
LOG_FILE = _BASE / "logs" / "morphix.log"
ANALYTICS_CHARTS_DIR = _BASE / "charts" / "analytics"


class PathResolver:
    """Provee rutas canónicas para todos los subsistemas."""

    @staticmethod
    def memory_base() -> Path:
        return MEMORY_BASE

    @staticmethod
    def memory_dir(workspace: str) -> Path:
        return MEMORY_BASE / workspace

    @staticmethod
    def code_projects_dir(workspace: str, project_root: str | None = None) -> Path:
        base = MEMORY_BASE / workspace
        if project_root:
            base = base / project_root
        return base

    @staticmethod
    def workspaces_base() -> Path:
        return WORKSPACES_BASE

    @staticmethod
    def workspace_dir(workspace: str) -> Path:
        return WORKSPACES_BASE / workspace

    @staticmethod
    def workspace_agents_dir(workspace: str) -> Path:
        return WORKSPACES_BASE / workspace / "agents"

    @staticmethod
    def workspace_hooks_dir(workspace: str) -> Path:
        return WORKSPACES_BASE / workspace / "hooks"

    @staticmethod
    def mcp_servers_file(workspace: str) -> Path:
        return WORKSPACES_BASE / workspace / "mcp_servers.json"

    @staticmethod
    def workspace_tools_dir(workspace: str) -> Path:
        return WORKSPACES_BASE / workspace / "tools"

    @staticmethod
    def workspace_workflows_dir(workspace: str) -> Path:
        return WORKSPACES_BASE / workspace / "workflows"

    @staticmethod
    def templates_dir() -> Path:
        return TEMPLATES_DIR

    @staticmethod
    def templates_agents_dir() -> Path:
        return TEMPLATES_DIR / "agents"

    @staticmethod
    def templates_hooks_dir() -> Path:
        return TEMPLATES_DIR / "hooks"

    @staticmethod
    def templates_workflows_dir() -> Path:
        return TEMPLATES_DIR / "workflows"

    @staticmethod
    def charts_dir() -> Path:
        return CHARTS_DIR

    @staticmethod
    def exports_dir() -> Path:
        return EXPORTS_DIR

    @staticmethod
    def log_file() -> Path:
        return LOG_FILE

    @staticmethod
    def analytics_charts_dir() -> Path:
        return ANALYTICS_CHARTS_DIR

    @staticmethod
    def normalize_path(file_path: str, project_root: str | None = None) -> str:
        """Normaliza una ruta relativa eliminando el prefijo project_root si está presente.

        Casos:
          file_path='code_projects/miapp/src/main.py', project_root='code_projects/miapp'
            → 'src/main.py'
          file_path='miapp/src/main.py', project_root='code_projects/miapp'
            → 'src/main.py' (el último componente 'miapp' se elimina)
          file_path='src/main.py', project_root='code_projects/miapp'
            → 'src/main.py' (sin cambios)

        Fuente única de verdad para normalización de rutas en todo el sistema.
        """
        if not project_root:
            return file_path

        project_parts = Path(project_root).parts
        path_parts = Path(file_path).parts

        # Case 1: path starts with full project_root
        if (
            len(path_parts) >= len(project_parts)
            and path_parts[: len(project_parts)] == project_parts
        ):
            relative_parts = path_parts[len(project_parts) :]
            return "/".join(relative_parts) if relative_parts else "."

        # Case 2: path starts with the last component of project_root (project name)
        last_part = project_parts[-1]  # ej: "miapp"
        if path_parts and path_parts[0] == last_part:
            relative_parts = path_parts[1:]
            return "/".join(relative_parts) if relative_parts else "."

        return file_path

    @staticmethod
    def normalize_project_root(project_root: str | None) -> str | None:
        """Asegura que project_root tenga el prefijo 'code_projects/'."""
        if project_root and not str(project_root).startswith("code_projects/"):
            return f"code_projects/{project_root}"
        return project_root


paths = PathResolver()
