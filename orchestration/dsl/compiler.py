# orchestration/dsl/compiler.py
"""Compiler YAML→IR del DSL.

Compila un documento raw (dict YAML) a `CompiledWorkflow`: el árbol validado
(`WorkflowDSL`) + includes resueltos recursivamente (con detección de ciclos
y tope de profundidad) + el mapa id→path de todos los nodos (para pausas con
pila y para el naming `padre ▸ hijo` del emitter).

Regla de coexistencia con templates legacy: un YAML es DSL **solo si** tiene
campo `version`; sin `version` es template legacy (loader.py lo ignora aquí,
este catálogo ignora los legacy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from orchestration.dsl.schema import (
    DecideStep,
    LoopStep,
    ParallelStep,
    WorkflowDSL,
    WorkflowStep,
)

MAX_INCLUDE_DEPTH = 3

__all__ = [
    "CompiledWorkflow",
    "PresetCatalog",
    "FileSystemCatalog",
    "compile_workflow",
    "compile_file",
    "is_dsl_document",
]


class PresetCatalog(Protocol):
    """Fuente de presets DSL por nombre. load() → dict raw o None."""

    def load(self, name: str) -> dict[str, Any] | None: ...


class FileSystemCatalog:
    """Catálogo real: workspace-local primero, luego templates globales.

    Solo considera YAMLs DSL (con campo `version`); los legacy son invisibles.
    """

    def __init__(self, workspace: str | None = None):
        self._workspace = workspace

    def _candidate_paths(self, name: str) -> list[Path]:
        from core.path_resolver import paths

        candidates: list[Path] = []
        if self._workspace:
            candidates.append(paths.workspace_workflows_dir(self._workspace) / f"{name}.yaml")
            candidates.append(
                paths.template_workspace_workflows_dir(self._workspace) / f"{name}.yaml"
            )
        candidates.append(paths.templates_workflows_dir() / f"{name}.yaml")
        return candidates

    def load(self, name: str) -> dict[str, Any] | None:
        for path in self._candidate_paths(name):
            if not path.exists():
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            if isinstance(data, dict) and "version" in data:
                return data
        return None


def is_dsl_document(raw: Any) -> bool:
    """True si el documento raw es un DSL (campo `version` presente)."""
    return isinstance(raw, dict) and "version" in raw


@dataclass
class CompiledWorkflow:
    """IR ejecutable: dsl validado + includes resueltos + mapa de paths."""

    dsl: WorkflowDSL
    includes: dict[str, CompiledWorkflow] = field(default_factory=dict)
    node_paths: dict[str, str] = field(default_factory=dict)


def _build_paths(dsl: WorkflowDSL, node_paths: dict[str, str]) -> None:
    """Puebla id → path para todos los nodos (recursivo)."""

    def walk(steps: list[Any], prefix: str) -> None:
        for step in steps:
            path = step.id if not prefix else f"{prefix} ▸ {step.id}"
            node_paths[step.id] = path
            if isinstance(step, LoopStep):
                walk(step.body, path)
            elif isinstance(step, ParallelStep):
                for branch in step.branches:
                    walk(branch.steps, path)
            elif isinstance(step, DecideStep):
                for option, branch_steps in step.branches.items():
                    walk(branch_steps, f"{path} ▸ {option}")

    walk(dsl.steps, "")


def _collect_includes(steps: list[Any]) -> list[str]:
    names: list[str] = []
    for step in steps:
        if isinstance(step, WorkflowStep):
            names.append(step.name)
        elif isinstance(step, LoopStep):
            names.extend(_collect_includes(step.body))
        elif isinstance(step, ParallelStep):
            for branch in step.branches:
                names.extend(_collect_includes(branch.steps))
        elif isinstance(step, DecideStep):
            for branch_steps in step.branches.values():
                names.extend(_collect_includes(branch_steps))
    return names


def compile_workflow(
    raw: dict[str, Any],
    *,
    catalog: PresetCatalog | None = None,
    _depth: int = 0,
    _stack: tuple[str, ...] = (),
) -> CompiledWorkflow:
    """Valida el documento, resuelve includes y construye el IR.

    Fail-loud: schema inválido → ValidationError de pydantic; include
    inexistente/cíclico/profundo → ValueError.
    """
    dsl = WorkflowDSL.model_validate(raw)
    name = dsl.name or "<anonimo>"

    if _depth > MAX_INCLUDE_DEPTH:
        raise ValueError(f"Include '{name}' excede la profundidad máxima ({MAX_INCLUDE_DEPTH})")
    if name in _stack:
        chain = " → ".join([*_stack, name])
        raise ValueError(f"Ciclo de includes detectado: {chain}")

    node_paths: dict[str, str] = {}
    _build_paths(dsl, node_paths)

    includes: dict[str, CompiledWorkflow] = {}
    for include_name in _collect_includes(dsl.steps):
        if include_name in includes:
            continue
        if catalog is None:
            raise ValueError(
                f"Include '{include_name}' declarado pero no hay catálogo para resolverlo"
            )
        child_raw = catalog.load(include_name)
        if child_raw is None:
            raise ValueError(f"No existe el preset DSL '{include_name}' (include desde '{name}')")
        includes[include_name] = compile_workflow(
            child_raw,
            catalog=catalog,
            _depth=_depth + 1,
            _stack=(*_stack, name),
        )

    return CompiledWorkflow(dsl=dsl, includes=includes, node_paths=node_paths)


def compile_file(path: Path, *, catalog: PresetCatalog | None = None) -> CompiledWorkflow:
    """Lee un YAML DSL del disco y lo compila."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not is_dsl_document(raw):
        raise ValueError(f"'{path}' no es un documento DSL (falta campo 'version')")
    return compile_workflow(raw, catalog=catalog)
