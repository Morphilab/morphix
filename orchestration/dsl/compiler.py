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
            except yaml.YAMLError as e:
                # un archivo EXISTENTE con YAML roto es un error de edición
                # real: tragárselo reportaría "No existe el preset/fragmento"
                # — el diagnóstico del flujo EDITAR no puede mentir.
                raise ValueError(f"YAML inválido en '{path}': {e}") from e
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


def _contains_fragment(steps_raw: list[dict]) -> bool:
    for s in steps_raw:
        if s.get("kind") == "fragment":
            return True
        if isinstance(s.get("body"), list) and _contains_fragment(s["body"]):
            return True
        if isinstance(s.get("branches"), list):
            if any(_contains_fragment(br.get("steps") or []) for br in s["branches"]):
                return True
        if isinstance(s.get("branches"), dict):
            if any(_contains_fragment(v) for v in s["branches"].values()):
                return True
    return False


def _expand_fragment_steps(
    steps_raw: list[dict], *, catalog: Any, stack: tuple[str, ...], depth: int
) -> list[dict]:
    """Expande steps ``fragment`` in-place (recursivo en bodies/branches).

    Fail-loud: fragmento inexistente, cíclico o profundo → ValueError. Los
    steps del fragmento se validan contra el union ANTES del splice; el
    documento completo se re-valida tras la expansión (ids únicos incluidos).
    """
    from pydantic import TypeAdapter

    from orchestration.dsl.schema import StepSpec

    adapter: TypeAdapter[Any] = TypeAdapter(StepSpec)  # type: ignore[assignment]
    out: list[dict] = []
    for s in steps_raw:
        if s.get("kind") == "fragment":
            include = str(s.get("include") or "")
            if not include:
                raise ValueError(f"fragment '{s.get('id')}' sin 'include'")
            if include in stack:
                chain = " → ".join([*stack, include])
                raise ValueError(f"Ciclo de includes detectado: {chain}")
            if depth > MAX_INCLUDE_DEPTH:
                raise ValueError(
                    f"Include '{include}' excede la profundidad máxima ({MAX_INCLUDE_DEPTH})"
                )
            frag_raw = catalog.load(include)
            if frag_raw is None:
                origen = stack[-1] if stack else "<raíz>"
                raise ValueError(f"No existe el fragmento '{include}' (include desde '{origen}')")
            frag_steps = frag_raw.get("steps") if isinstance(frag_raw, dict) else frag_raw
            if not isinstance(frag_steps, list):
                raise ValueError(f"fragmento '{include}' sin lista de steps")
            for fs in frag_steps:
                adapter.validate_python(fs)
            out.extend(
                _expand_fragment_steps(
                    list(frag_steps), catalog=catalog, stack=(*stack, include), depth=depth + 1
                )
            )
        else:
            if isinstance(s.get("body"), list):
                s["body"] = _expand_fragment_steps(
                    s["body"], catalog=catalog, stack=stack, depth=depth
                )
            if isinstance(s.get("branches"), list):
                for br in s["branches"]:
                    if isinstance(br.get("steps"), list):
                        br["steps"] = _expand_fragment_steps(
                            br["steps"], catalog=catalog, stack=stack, depth=depth
                        )
            if isinstance(s.get("branches"), dict):
                s["branches"] = {
                    k: _expand_fragment_steps(v, catalog=catalog, stack=stack, depth=depth)
                    for k, v in s["branches"].items()
                }
            out.append(s)
    return out


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

    # Includes de body: el step `fragment` se expande in-place en compile-time
    # y el documento se re-valida — el motor y el validador semántico solo ven
    # steps reales (la deduplicación de gemelos pasa a ser por construcción).
    raw_steps = [s.model_dump() for s in dsl.steps]
    if _contains_fragment(raw_steps):
        if catalog is None:
            raise ValueError("fragment declarado pero no hay catálogo para resolverlo")
        expanded = _expand_fragment_steps(raw_steps, catalog=catalog, stack=(name,), depth=_depth)
        dsl = WorkflowDSL.model_validate({**raw, "steps": expanded})

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
