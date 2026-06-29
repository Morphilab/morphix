# features/maestro/services/workflow_loader.py
import logging

import yaml

from core.path_resolver import paths

logger = logging.getLogger(__name__)

GLOBAL_TEMPLATES_DIR = paths.templates_workflows_dir()


def list_workflows(workspace_name: str | None = None) -> list[str]:
    """Return workflow names available in the workspace.
    Workspace-local workflows take priority; global templates are fallback."""
    workflows: set[str] = set()

    # 1. Workspace-local workflows (primary source)
    if workspace_name:
        local_dir = paths.workspace_workflows_dir(workspace_name)
        if local_dir.exists():
            for f in local_dir.glob("*.yaml"):
                workflows.add(f.stem)

    # 2. Fallback: global templates only if workspace has no workflows
    if not workflows:
        if GLOBAL_TEMPLATES_DIR.exists():
            for f in GLOBAL_TEMPLATES_DIR.glob("*.yaml"):
                workflows.add(f.stem)

    return sorted(workflows)


def load_workflow_template(
    workspace_name: str | None = None, workflow_name: str = "development"
) -> dict:
    """
    Carga la plantilla de workflow indicada.
    Busca primero en el workspace local y luego en global.
    """
    local_template = None
    if workspace_name:
        local_path = paths.workspace_workflows_dir(workspace_name) / f"{workflow_name}.yaml"
        if local_path.exists():
            try:
                with open(local_path, encoding="utf-8") as f:
                    local_template = yaml.safe_load(f)
                logger.info(
                    f"✅ Plantilla '{workflow_name}' cargada desde workspace '{workspace_name}'"
                )
            except Exception as e:
                logger.error(f"Error cargando plantilla local {local_path}: {e}")

    if local_template is None:
        global_path = GLOBAL_TEMPLATES_DIR / f"{workflow_name}.yaml"
        if global_path.exists():
            try:
                with open(global_path, encoding="utf-8") as f:
                    local_template = yaml.safe_load(f)
                logger.info(f"✅ Plantilla '{workflow_name}' global cargada")
            except Exception as e:
                logger.error(f"Error cargando plantilla global {global_path}: {e}")

    return local_template or {}
