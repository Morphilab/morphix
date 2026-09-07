# tools/loader.py
import importlib.util
import logging
import sys
from pathlib import Path

from core.path_resolver import paths

logger = logging.getLogger(__name__)

# Store loaded module names per workspace so we can unload them
_workspace_modules: dict[str, dict[str, list[str]]] = {}  # nombre registrado por módulo


def _import_module_from_file(name: str, file_path: Path) -> bool:
    """Importa un módulo desde un archivo .py. Retorna True si tuvo éxito.

    Registra el módulo en sys.modules ANTES de ejecutarlo — sin esto,
    un import canónico posterior (`import tools.orchestrator`) re-ejecutaría
    el archivo produciendo módulos/singletons duplicados.

    Si el módulo YA está en sys.modules cargado desde el
    MISMO archivo (import canónico previo), NO re-ejecutar — re-ejecutar
    crearía un segundo dict de globals y duplicaría registros/estado.
    """
    try:
        existing = sys.modules.get(name)
        existing_file = getattr(existing, "__file__", None)
        if (
            existing is not None
            and existing_file is not None
            and Path(existing_file).resolve() == file_path.resolve()
        ):
            return True
        spec = importlib.util.spec_from_file_location(name, file_path)
        if spec is None or spec.loader is None:
            logger.error(f"No se pudo crear spec para {file_path}")
            return False
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)  # no dejar módulos a medio inicializar
            raise
        return True
    except Exception as e:
        logger.error(f"Error loading tool {file_path}: {e}")
        return False


# módulos de INFRAESTRUCTURA — nunca re-ejecutarlos vía glob.
# Re-executar registry.py aquí SWAP-eaba la instancia del registro a mitad
# de carga (mitad de las tools en la instancia vieja, mitad en la nueva).
_INFRA_MODULES = frozenset(
    {"__init__", "_subprocess", "loader", "registry", "specs", "wrapper", "orchestrator"}
)


def load_global_tools():
    """Carga las herramientas globales desde la carpeta 'tools/'."""
    global_dir = Path(__file__).parent.parent / "tools"
    if not global_dir.exists():
        logger.info("No se encontró el directorio de herramientas globales.")
        return
    for py_file in global_dir.glob("*.py"):
        if py_file.name.startswith("_") or py_file.stem in _INFRA_MODULES:
            continue
        logger.info(f"Cargando herramienta global: {py_file.name}")
        _import_module_from_file(f"tools.{py_file.stem}", py_file)


def load_workspace_tools(workspace: str):
    """Carga herramientas locales desde workspaces/<workspace>/tools/."""
    from tools.registry import tools_registry

    local_dir = paths.workspace_tools_dir(workspace)
    if not local_dir.exists():
        logger.info(f"No hay herramientas locales en workspace '{workspace}'")
        return
    # snapshotear los nombres REALMENTE registrados por cada módulo
    # (diff del registry) — el stem del filename NO es la fuente de verdad.
    loaded: dict[str, list[str]] = {}
    for py_file in local_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        full_name = f"workspaces.{workspace}.tools.{py_file.stem}"
        before = set(tools_registry.list_tools())
        if _import_module_from_file(full_name, py_file):
            registered = sorted(set(tools_registry.list_tools()) - before)
            loaded[full_name] = registered
            for name in registered:
                logger.debug(f"Workspace tool registrada: {name} (desde {py_file.name})")
    _workspace_modules[workspace] = loaded


def unload_workspace_tools():
    """Elimina del registro las herramientas del workspace anterior."""
    from tools.registry import tools_registry

    for workspace_name, modules in list(_workspace_modules.items()):
        for full_name, registered_names in modules.items():
            for tool_name in registered_names:
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
