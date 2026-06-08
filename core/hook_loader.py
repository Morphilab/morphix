# core/hook_loader.py
import importlib.util
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from core.path_resolver import paths

logger = logging.getLogger(__name__)

# Track workspace hooks as (hook_point, callable) pairs per workspace
_workspace_hook_refs: dict[str, list[tuple[str, Callable]]] = {}
_workspace_hook_modules: dict[str, list[str]] = {}


def _import_module_from_file(name: str, file_path: Path) -> bool:
    """Import a .py module from disk. Returns True on success."""
    try:
        spec = importlib.util.spec_from_file_location(name, file_path)
        if spec is None or spec.loader is None:
            logger.error(f"Could not create spec for {file_path}")
            return False
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return True
    except Exception:
        logger.error(f"Error loading hook {file_path}", exc_info=True)
        return False


def load_global_hooks() -> None:
    """Load global hooks from core/hooks/."""
    global_dir = Path(__file__).parent / "hooks"
    if not global_dir.exists():
        logger.info("No global hooks directory found")
        return
    for py_file in sorted(global_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        logger.info(f"Loading global hook: {py_file.name}")
        _import_module_from_file(f"core.hooks.{py_file.stem}", py_file)


def load_workspace_hooks(workspace: str) -> None:
    """Load workspace-local hooks from workspaces/<name>/hooks/.
    Uses pre/post snapshot diff to track which hooks were added by the workspace.
    """
    from core.hooks_registry import hooks_registry

    local_dir = paths.workspace_hooks_dir(workspace)
    if not local_dir.exists():
        logger.info(f"No hooks directory for workspace '{workspace}'")
        return

    # Snapshot current registry state
    pre_snapshot = {
        hook_point: list(handlers) for hook_point, handlers in hooks_registry._hooks.items()
    }

    modules_loaded: list[str] = []
    for py_file in sorted(local_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        full_name = f"workspaces.{workspace}.hooks.{py_file.stem}"
        if _import_module_from_file(full_name, py_file):
            modules_loaded.append(full_name)

    # Diff: find newly registered hooks
    post_snapshot = {
        hook_point: list(handlers) for hook_point, handlers in hooks_registry._hooks.items()
    }

    workspace_refs: list[tuple[str, Callable]] = []
    for hook_point, handlers in post_snapshot.items():
        pre_handlers = pre_snapshot.get(hook_point, [])
        for handler in handlers:
            if handler not in pre_handlers:
                workspace_refs.append((hook_point, handler))

    _workspace_hook_refs[workspace] = workspace_refs
    _workspace_hook_modules[workspace] = modules_loaded
    logger.info(f"Loaded {len(workspace_refs)} workspace hook(s) for '{workspace}'")


def unload_workspace_hooks() -> None:
    """Remove only workspace-scoped hooks from registry and sys.modules."""
    from core.hooks_registry import hooks_registry

    for workspace_name, refs in list(_workspace_hook_refs.items()):
        for hook_point, func in refs:
            hooks_registry.unregister(hook_point, func)
        logger.debug(f"Unloaded {len(refs)} hook(s) from workspace '{workspace_name}'")

    for _workspace_name, module_names in list(_workspace_hook_modules.items()):
        for full_name in module_names:
            if full_name in sys.modules:
                del sys.modules[full_name]

    _workspace_hook_refs.clear()
    _workspace_hook_modules.clear()
