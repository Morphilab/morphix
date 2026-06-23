# core/tool_loader.py
import importlib.util
import logging
from pathlib import Path

from core.path_resolver import paths

logger = logging.getLogger(__name__)

# Store loaded module names per workspace so we can unload them
_workspace_modules: dict[str, list[str]] = {}


def _import_module_from_file(name: str, file_path: Path) -> bool:
    """Importa un módulo desde un archivo .py. Retorna True si tuvo éxito."""
    try:
        spec = importlib.util.spec_from_file_location(name, file_path)
        if spec is None or spec.loader is None:
            logger.error(f"No se pudo crear spec para {file_path}")
            return False
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return True
    except Exception as e:
        logger.error(f"Error loading tool {file_path}: {e}")
        return False


def load_global_tools():
    """Carga las herramientas globales desde la carpeta 'tools/'."""
    global_dir = Path(__file__).parent.parent / "tools"
    if not global_dir.exists():
        logger.info("No se encontró el directorio de herramientas globales.")
        return
    for py_file in global_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        logger.info(f"Cargando herramienta global: {py_file.name}")
        _import_module_from_file(f"tools.{py_file.stem}", py_file)


def load_workspace_tools(workspace: str):
    """Carga herramientas locales desde workspaces/<workspace>/tools/."""
    local_dir = paths.workspace_tools_dir(workspace)
    if not local_dir.exists():
        logger.info(f"No hay herramientas locales en workspace '{workspace}'")
        return
    modules_loaded = []
    for py_file in local_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        full_name = f"workspaces.{workspace}.tools.{py_file.stem}"
        if _import_module_from_file(full_name, py_file):
            modules_loaded.append(full_name)
    _workspace_modules[workspace] = modules_loaded


def unload_workspace_tools():
    """Elimina del registro las herramientas del workspace anterior."""
    from tools.registry import tools_registry

    for workspace_name, module_names in list(_workspace_modules.items()):
        for full_name in module_names:
            tool_name = full_name.rsplit(".", 1)[-1]
            tools_registry.unregister(tool_name)
            logger.debug(f"Herramienta descargada: {tool_name} (workspace: {workspace_name})")
            if full_name in _imported_module_names():
                _cleanup_module(full_name)
    _workspace_modules.clear()
    logger.info("Herramientas de workspace descargadas del registro")


def _imported_module_names() -> set:
    import sys

    return {m for m in sys.modules if m.startswith("workspaces.")}


def _cleanup_module(full_name: str):
    import sys

    if full_name in sys.modules:
        del sys.modules[full_name]
