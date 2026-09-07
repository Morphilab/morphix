# orchestration/loader.py
"""Carga de documentos de workflow.

Desde el retiro del legacy solo existe el formato DSL
(docs `version: 1`): `load_workflow_document` lee el YAML crudo y el
compilador DSL (`orchestration.dsl.compiler`) valida y compila. Las
plantillas legacy (`WorkflowTemplate`) fueron eliminadas.
"""

import logging
from pathlib import Path

import yaml

from core.path_resolver import paths

logger = logging.getLogger(__name__)


def _workflow_yaml_files(directory: Path) -> list[Path]:
    """YAMLs de workflow del directorio, ignorando archivos `_`-prefijados."""
    if not directory.exists():
        return []
    return sorted(f for f in directory.glob("*.yaml") if not f.name.startswith("_"))


def list_workflows(workspace_name: str | None = None) -> list[str]:
    """Return workflow names available in the workspace.
    Workspace-local workflows take priority; global templates are fallback."""
    workflows: set[str] = set()

    # 1. Workspace-local workflows (primary source)
    if workspace_name:
        local_dir = paths.workspace_workflows_dir(workspace_name)
        for f in _workflow_yaml_files(local_dir):
            workflows.add(f.stem)

    # 2. Fallback: global templates only if workspace has no workflows
    if not workflows:
        for f in _workflow_yaml_files(paths.templates_workflows_dir()):
            workflows.add(f.stem)

    return sorted(workflows)


def load_workflow_document(
    workspace_name: str | None = None, workflow_name: str | None = None
) -> dict | None:
    """Lee el YAML CRUDO (sin validar) del workflow indicado.

    Devuelve None si no existe. El dispatcher decide si el documento es DSL
    (campo ``version``) y lo compila con `orchestration.dsl.compiler`.
    """
    if workflow_name is None:
        workflow_name = "development"
    candidates: list[Path] = []
    if workspace_name:
        candidates.append(paths.workspace_workflows_dir(workspace_name) / f"{workflow_name}.yaml")
        candidates.append(
            paths.template_workspace_workflows_dir(workspace_name) / f"{workflow_name}.yaml"
        )
    candidates.append(paths.templates_workflows_dir() / f"{workflow_name}.yaml")
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"YAML inválido en '{path}': {e}") from e
        if isinstance(data, dict) and data:
            return data
    return None


def expand_dsl_project_root(root: str | None) -> str | None:
    """project.root portable — expande ${VAR:-default} y ~ en el punto
    único de carga DSL (todos los consumidores reciben la ruta resuelta)."""
    if root and ("${" in root or "~" in root):
        from core.utils import expand_env_path

        return expand_env_path(root)
    return root
