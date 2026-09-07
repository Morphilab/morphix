# orchestration/dsl/validator.py
"""Validador semántico del IR compilado.

El schema (structural) vive en schema.py; AQUÍ vive la semántica contra el
contexto de ejecución:

- agentes referenciados ∈ ``agents.allowed`` (deny-by-default)
- tools referenciadas (steps + ``until``) ∈ ``tools.allowed``
- variables ``$var`` definidas (inputs/defaults/outputs/reservadas)
- ``depends_on`` de parallel referencian ids hermanos existentes
- contrato I/O de includes: inputs/outputs del caller ⊆ contrato del hijo
- ``aggregate: moderator`` requiere agente declarado y permitido

Devuelve lista de errores legibles (vacía = válido). Determinista: nunca
consulta LLM ni registros vivos — los allowlists vienen del propio documento.
"""

from __future__ import annotations

import re
from typing import Any

from orchestration.dsl.compiler import CompiledWorkflow
from orchestration.dsl.schema import (
    AgentStep,
    AggregateStep,
    DecideStep,
    DecomposeStep,
    LoopStep,
    ParallelStep,
    PluginStep,
    ToolStep,
    WorkflowStep,
)

__all__ = ["validate_workflow"]

_VAR_REF_RE = re.compile(r"\$([a-z_][a-z0-9_]*)")

# Siempre disponibles en cualquier interpolación.
# iter/max_iter: definidos DENTRO de loops — como `item`, su uso fuera
# de un loop falla en runtime con EngineError (fail-loud del motor).
RESERVED_VARS = frozenset({"query", "item", "last_gate", "last_output", "iter", "max_iter"})


def _defined_vars(dsl: Any) -> set[str]:
    """Variables definidas estáticamente: inputs, defaults, outputs de steps."""
    defined: set[str] = set(dsl.inputs) | set(dsl.defaults.keys()) | set(RESERVED_VARS)

    def walk(steps: list[Any]) -> None:
        for step in steps:
            if isinstance(step, AgentStep):
                defined.update(step.outputs)
            elif isinstance(step, ToolStep):
                defined.update(step.outputs.keys())
            elif isinstance(step, DecomposeStep):
                defined.add(step.output)
            elif isinstance(step, PluginStep):
                defined.update(step.outputs)
            elif isinstance(step, LoopStep):
                defined.update(step.init_vars.keys())
                walk(step.body)
            elif isinstance(step, ParallelStep):
                for branch in step.branches:
                    walk(branch.steps)
            elif isinstance(step, DecideStep):
                for branch_steps in step.branches.values():
                    walk(branch_steps)
            elif isinstance(step, WorkflowStep):
                defined.update(step.outputs)

    walk(dsl.steps)
    return defined


def _iter_strings(value: Any) -> list[str]:
    """Todos los strings embebidos en un valor (dict/list anidados)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for v in value.values():
            out.extend(_iter_strings(v))
        return out
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_iter_strings(v))
        return out
    return []


def _check_var_refs(steps: list[Any], defined: set[str], errors: list[str]) -> None:
    """Recorre los steps buscando $refs indefinidas en campos interpolables."""
    for step in steps:
        path = step.id
        candidates: list[str] = []
        if isinstance(step, AgentStep):
            candidates.append(step.goal)
            if step.when:
                candidates.append(step.when)
        if isinstance(step, ToolStep):
            candidates.extend(_iter_strings(step.args))
            if step.when:
                candidates.append(step.when)
        if isinstance(step, DecideStep):
            candidates.append(step.question)
        if isinstance(step, LoopStep):
            if step.over and step.over not in defined:
                errors.append(f"{path}: loop.over referencia '${step.over}' que no está definida")
            if step.until is not None and step.until.type == "agent":
                candidates.append(step.until.question)
            _check_var_refs(step.body, defined, errors)
        if isinstance(step, ParallelStep):
            for branch in step.branches:
                _check_var_refs(branch.steps, defined, errors)
        if isinstance(step, DecideStep):
            for branch_steps in step.branches.values():
                _check_var_refs(branch_steps, defined, errors)
        if isinstance(step, WorkflowStep):
            candidates.extend(_iter_strings(step.inputs))

        for text in candidates:
            for match in _VAR_REF_RE.finditer(text):
                var = match.group(1)
                if var not in defined:
                    errors.append(f"{path}: variable '${var}' no está definida (en: {text[:80]!r})")


def _collect_step_ids(steps: list[Any]) -> set[str]:
    """Ids de todos los steps del árbol (recursivo)."""
    ids: set[str] = set()
    for step in steps:
        ids.add(step.id)
        if isinstance(step, LoopStep):
            ids |= _collect_step_ids(step.body)
        elif isinstance(step, ParallelStep):
            for branch in step.branches:
                ids |= _collect_step_ids(branch.steps)
        elif isinstance(step, DecideStep):
            for branch_steps in step.branches.values():
                ids |= _collect_step_ids(branch_steps)
    return ids


def validate_workflow(cw: CompiledWorkflow) -> list[str]:
    """Valida el workflow compilado (raíz + includes). Vacía = válido."""
    errors: list[str] = []

    def validate_one(compiled: CompiledWorkflow) -> None:
        dsl = compiled.dsl
        allowed_agents = set(dsl.agents.allowed)
        allowed_tools = set(dsl.tools.allowed or [])
        defined = _defined_vars(dsl)

        # commit_after: los ids declarados deben existir en el árbol
        step_ids = _collect_step_ids(dsl.steps)
        for cid in dsl.commit_after:
            if cid not in step_ids:
                errors.append(f"commit_after: id '{cid}' no existe entre los steps del workflow")

        def walk(steps: list[Any]) -> None:
            for step in steps:
                if isinstance(step, AgentStep):
                    if step.agent not in allowed_agents:
                        errors.append(f"{step.id}: agente '{step.agent}' no está en agents.allowed")
                elif isinstance(step, ToolStep):
                    if step.tool not in allowed_tools:
                        errors.append(f"{step.id}: tool '{step.tool}' no está en tools.allowed")
                elif isinstance(step, LoopStep):
                    if step.until is not None and step.until.type in ("tool", "metric"):
                        tool = step.until.tool
                        if tool not in allowed_tools:
                            errors.append(
                                f"{step.id}: until.tool '{tool}' no está en tools.allowed"
                            )
                    if step.parallel and not step.over:
                        errors.append(
                            f"{step.id}: loop.parallel requiere 'over' (iteración sobre lista)"
                        )
                    if step.parallel and step.until is not None:
                        errors.append(
                            f"{step.id}: loop.parallel no soporta 'until' "
                            "(early-exit ambiguo con ítems en vuelo)"
                        )
                    walk(step.body)
                elif isinstance(step, ParallelStep):
                    sibling_ids = {s.id for branch in step.branches for s in branch.steps}
                    for branch in step.branches:
                        for dep in branch.depends_on:
                            if dep not in sibling_ids:
                                errors.append(
                                    f"{step.id}: depends_on '{dep}' no existe entre los steps del parallel"
                                )
                        walk(branch.steps)
                elif isinstance(step, DecideStep):
                    for branch_steps in step.branches.values():
                        walk(branch_steps)
                elif isinstance(step, AggregateStep):
                    if step.strategy == "moderator":
                        if not step.agent:
                            errors.append(f"{step.id}: aggregate moderator requiere 'agent'")
                        elif step.agent not in allowed_agents:
                            errors.append(
                                f"{step.id}: agente moderador '{step.agent}' no está en agents.allowed"
                            )
                elif isinstance(step, WorkflowStep):
                    child = compiled.includes.get(step.name)
                    if child is not None:
                        child_inputs = set(child.dsl.inputs)
                        for input_name in step.inputs:
                            if input_name not in child_inputs:
                                errors.append(
                                    f"{step.id}: el preset '{step.name}' no declara input '{input_name}'"
                                )
                        child_outputs = set(child.dsl.outputs)
                        for out_name in step.outputs:
                            if out_name not in child_outputs:
                                errors.append(
                                    f"{step.id}: el preset '{step.name}' no declara output '{out_name}'"
                                )

        walk(dsl.steps)
        _check_var_refs(dsl.steps, defined, errors)

        for child in compiled.includes.values():
            validate_one(child)

    validate_one(cw)
    return errors
