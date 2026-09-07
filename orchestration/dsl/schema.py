# orchestration/dsl/schema.py
"""Schema pydantic del DSL de workflows (v1).

Principio: el YAML es el contrato, `extra="forbid"` en todos los niveles.
Validación STRUCTURAL aquí (formas, bounds, enums); la semántica (agentes
existen, includes sin ciclos, variables definidas) vive en `validator.py`.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Reutiliza los configs del template legacy — misma semántica deny-by-default.
from orchestration.template_schema import AgentsConfig, ProjectConfig, ToolsConfig

_VAR_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# ── Hasta (condiciones de salida de loops) ─────────────────────────────────


class UntilToolSpec(BaseModel):
    """Salida evaluada por una tool: `check` es la clave del resultado que
    debe ser truthy (p.ej. `tests_all_pass` de test_runner)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tool"]
    tool: str
    check: str
    # argumentos de la tool, interpolables con $var por el
    # motor. Sin esto el until solo puede invocar tools zero-arg —
    # cualquier tool con parámetros requeridos moriría en TypeError
    # y el loop agotaría max_iter.
    args: dict[str, Any] = Field(default_factory=dict)


class UntilAgentSpec(BaseModel):
    """Salida por juicio del modelo — no-determinismo ACOTADO: el modelo
    responde dentro de un enum esperado; salida inválida aplica `fallback`
    determinista (fail: corta con error; exit: sale del loop)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["agent"]
    question: str
    expect: str
    fallback: Literal["fail", "exit"] = "fail"


class UntilMetricSpec(BaseModel):
    """Salida por métrica numérica sobre salida estructurada de una tool."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["metric"]
    tool: str
    metric: str
    op: Literal[">=", ">", "<=", "<", "=="]
    value: float
    # paridad con UntilToolSpec — ver comentario ahí.
    args: dict[str, Any] = Field(default_factory=dict)


UntilSpec = Annotated[
    UntilToolSpec | UntilAgentSpec | UntilMetricSpec,
    Field(discriminator="type"),
]


# ── Steps (unión discriminada por `kind`) ──────────────────────────────────


class _StepBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z_][a-z0-9_]*$")
    when: str | None = None  # "if_blockers" | "query_contains:X" | "$var"
    on_error: Literal["abort", "continue"] = "abort"


class AgentStep(_StepBase):
    kind: Literal["agent"]
    agent: str
    goal: str = ""
    model_role: str | None = None
    retry_max: int = Field(default=0, ge=0, le=3)
    timeout: int = Field(default=300, ge=10)
    # gate: regex a compilar sobre el output del agente → setea $last_gate.
    # `true` = patrón bloqueante estándar `[(Bloqueante|FAIL]`.
    gate: str | bool = False
    outputs: list[str] = Field(default_factory=list)  # vars a exportar del resultado


class ToolStep(_StepBase):
    kind: Literal["tool"]
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    # var_name → clave del resultado de la tool
    outputs: dict[str, str] = Field(default_factory=dict)


class DecomposeStep(_StepBase):
    kind: Literal["decompose"]
    strategy: Literal["flat", "dag"]
    output: str = "subtasks"  # nombre de la variable lista


class Branch(BaseModel):
    """Rama de un parallel: sub-secuencia con dependencias opcionales."""

    model_config = ConfigDict(extra="forbid")

    steps: list[StepSpec] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class ParallelStep(_StepBase):
    kind: Literal["parallel"]
    branches: list[Branch] = Field(min_length=1)
    max_parallel: int = Field(default=2, ge=1, le=8)


class LoopStep(_StepBase):
    kind: Literal["loop"]
    max_iter: int = Field(ge=1, le=20)
    over: str | None = None  # variable lista a iterar (item → $item)
    until: UntilSpec | None = None  # salida temprana (corta TODO el loop)
    init_vars: dict[str, Any] = Field(default_factory=dict)  # valores iniciales del loop
    # parallel: body concurrente por ítem con vars aisladas por ítem.
    # Solo con over; NO combinable con until (early-exit ambiguo en vuelo).
    parallel: bool = False
    parallel_max: int = Field(default=2, ge=1, le=8)
    body: list[StepSpec] = Field(min_length=1)


class DecideStep(_StepBase):
    """No-determinismo acotado: el modelo elige DENTRO del enum `options`;
    salida inválida o ambigua → `fallback` determinista."""

    kind: Literal["decide"]
    question: str
    options: list[str] = Field(min_length=2)
    fallback: str
    model_role: str | None = None
    branches: dict[str, list[StepSpec]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_options(self) -> DecideStep:
        if len(set(self.options)) != len(self.options):
            raise ValueError("decide: options duplicados")
        if self.fallback not in self.options:
            raise ValueError(f"decide: fallback '{self.fallback}' debe ser uno de {self.options}")
        if set(self.branches.keys()) != set(self.options):
            missing = set(self.options) - set(self.branches.keys())
            extra = set(self.branches.keys()) - set(self.options)
            raise ValueError(
                f"decide: branches deben cubrir exactamente options "
                f"(faltan: {sorted(missing)}, sobran: {sorted(extra)})"
            )
        return self


class CheckpointStep(_StepBase):
    """Pausa humana persistida (PausedSession) antes de continuar."""

    kind: Literal["checkpoint"]
    question: str


class WorkflowStep(_StepBase):
    """Include de otro preset como subrutina, con contrato I/O explícito."""

    kind: Literal["workflow"]
    name: str
    inputs: dict[str, str] = Field(default_factory=dict)  # input_preset → "$var"
    outputs: list[str] = Field(default_factory=list)


class PluginStep(_StepBase):
    """Primitiva plugin registrada (orchestration/dsl/registry.py).

    Escape hatch de extensibilidad: semántica runtime nueva sin tocar el
    motor. El executor valida sus propios params (fail-loud). ``outputs``
    es CONTRATO declarativo para el validador (el plugin muta el estado
    por su cuenta vía params).
    """

    kind: Literal["plugin"]
    plugin: str
    params: dict[str, Any] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)


class AggregateStep(_StepBase):
    kind: Literal["aggregate"]
    strategy: Literal["result", "confidence", "moderator"]
    agent: str | None = None  # solo strategy=moderator


StepSpec = Annotated[
    AgentStep
    | ToolStep
    | DecomposeStep
    | ParallelStep
    | LoopStep
    | DecideStep
    | CheckpointStep
    | WorkflowStep
    | AggregateStep
    | PluginStep,
    Field(discriminator="kind"),
]


# ── Documento raíz ─────────────────────────────────────────────────────────


def _collect_ids(steps: list[Any], seen: dict[str, str]) -> None:
    """Recorre el árbol completo de steps y detecta ids duplicados."""
    for step in steps:
        if step.id in seen:
            raise ValueError(f"Id de step duplicado: '{step.id}'")
        seen[step.id] = step.kind
        if isinstance(step, LoopStep):
            _collect_ids(step.body, seen)
        elif isinstance(step, ParallelStep):
            for branch in step.branches:
                _collect_ids(branch.steps, seen)
        elif isinstance(step, DecideStep):
            for branch_steps in step.branches.values():
                _collect_ids(branch_steps, seen)


class WorkflowDSL(BaseModel):
    """Contrato completo de un workflow DSL (YAML)."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    name: str | None = None
    description: str = ""

    # Contrato I/O (para includes y para el caller)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    skills: bool = False
    commit_after: list[str] = Field(default_factory=list)

    steps: list[StepSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_doc(self) -> WorkflowDSL:
        # Identificadores válidos en el contrato I/O
        for field_name in ("inputs", "outputs"):
            for var in getattr(self, field_name):
                if not _VAR_RE.match(var):
                    raise ValueError(
                        f"{field_name}: identificador inválido '{var}' "
                        "(patrón ^[a-z_][a-z0-9_]*)"
                    )

        # Defaults solo sobre inputs declarados
        for key in self.defaults:
            if key not in self.inputs:
                raise ValueError(f"defaults: '{key}' no es un input declarado")

        # Ids únicos en todo el árbol (incluye nested loops/branches)
        _collect_ids(self.steps, {})

        # gate regex válida en AgentSteps (cualquier nivel)
        self._check_gates(self.steps)
        return self

    @classmethod
    def _check_gates(cls, steps: list[Any]) -> None:
        for step in steps:
            if isinstance(step, AgentStep) and isinstance(step.gate, str):
                try:
                    re.compile(step.gate)
                except re.error as e:
                    raise ValueError(f"gate: regex inválida en '{step.id}': {e}") from e
            if isinstance(step, LoopStep):
                cls._check_gates(step.body)
            elif isinstance(step, ParallelStep):
                for branch in step.branches:
                    cls._check_gates(branch.steps)
            elif isinstance(step, DecideStep):
                for branch_steps in step.branches.values():
                    cls._check_gates(branch_steps)


Branch.model_rebuild()
WorkflowDSL.model_rebuild()
