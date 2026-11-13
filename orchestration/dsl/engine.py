# orchestration/dsl/engine.py
"""Motor de ejecución del DSL — intérprete determinista del IR.

Principio: ``el modelo elige, el motor acota``. TODO lo difuso (agentes,
tools, descomposición, decisiones) pasa por ``EngineRuntime``; las salidas
del modelo se VALIDAN (enum de decide, expect de until-agent con fallback
determinista) y el control de flujo JAMÁS depende del modelo.

Pausa/resume: snapshot plano {vars, completed, loop_state, children}.
Las posiciones se cualifican por camino (``ciclo#2/impl``) para que el
resume no repita iteraciones ya ejecutadas.
"""

from __future__ import annotations

import asyncio
import datetime
import fnmatch
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.errors import MorphixError
from core.utils import truncar_en_palabra
from orchestration.dsl.compiler import CompiledWorkflow
from orchestration.dsl.run_state import (
    BudgetState,
    PlanItem,
    ReplanDecision,
    ReplanRecord,
    RunState,
    Verification,
)
from orchestration.dsl.schema import (
    AgentStep,
    AggregateStep,
    CheckpointStep,
    DecideStep,
    DecomposeStep,
    LoopStep,
    ParallelStep,
    PluginStep,
    StepSpec,
    ToolStep,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

# Patrón bloqueante estándar (paridad con pipeline legacy).
BLOCKER_RE = re.compile(r"\[(Bloqueante|FAIL)", re.IGNORECASE)

_VAR_SOLO_RE = re.compile(r"^\$([a-z_][a-z0-9_]*)$")
_VAR_REF_RE = re.compile(r"\$([a-z_][a-z0-9_]*)")

# Coste mínimo estimado de un ciclo de loop para el gate de presupuesto.
# No es una predicción: es el umbral por debajo del cual un ciclo nuevo
# arrancaría para morir en la cabecera del agent-loop.
_CYCLE_TOKEN_ESTIMATE = 8000

__all__ = ["WorkflowEngine", "EngineRuntime", "EngineResult", "EngineError", "AgentOutcome"]


class EngineError(MorphixError):
    """Violación de invariante interna del motor (fail-loud)."""

    code = "dsl/engine"


class PauseRequested(Exception):
    """Un step (adapter) pide pausa humana: clarificación, aprobación, etc.

    El motor NO conoce el mecanismo de persistencia — solo snapshota y
    devuelve status=paused con la pregunta. El wiring persiste
    (PausedSession) y en el resume re-entra por el mismo step.
    """

    def __init__(
        self,
        question: str,
        options: list[str] | None = None,
        kind: str = "clarification",
    ):
        self.question = question
        self.options = options or []
        self.kind = kind  # "clarification" | "checkpoint"
        super().__init__(question)


class _EnginePaused(Exception):
    """Señal interna: pausa humana. Transporta pregunta + origen del step."""

    def __init__(
        self,
        question: str = "",
        options: list[str] | None = None,
        kind: str = "checkpoint",
        step_id: str = "",
    ):
        self.question = question
        self.options = options or []
        self.kind = kind  # "checkpoint" | "clarification"
        self.step_id = step_id
        super().__init__(question or "paused")


class _EngineFailed(Exception):
    """Señal interna: un step falló con on_error=abort."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class _EngineStopped(Exception):
    """Señal interna: un gate con on_blocker=stop detiene el run."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class AgentOutcome:
    """Resultado estructurado de un agent-step: status real + evidencia."""

    status: str = "completed"
    text: str = ""
    files_written: list[str] = field(default_factory=list)
    iterations: int = 0
    tokens: int = 0
    error: str | None = None
    # valor crudo de fakes/consumidores que devuelven estructuras (una lista
    # de items como salida de un agent-step): `text` es la vista de prosa,
    # `raw` conserva el valor para `outputs`.
    raw: Any = None


def _as_outcome(value: Any) -> AgentOutcome:
    """Compatibilidad con fakes viejos que devuelven solo texto."""
    if isinstance(value, AgentOutcome):
        return value
    if isinstance(value, str):
        return AgentOutcome(status="completed", text=value, raw=value)
    return AgentOutcome(status="completed", text=str(value or ""), raw=value)


def _produces_ok(step: AgentStep, state: _State, files: list[str], qpath: str) -> bool:
    """¿Algún archivo escrito matchea algún glob de `produces`?"""
    if not step.produces:
        return True
    if not files:
        return False

    def _norm(value: str) -> str:
        text = str(value).replace("\\", "/")
        # solo el prefijo "./" es ruido; lstrip("./") se comía los puntos de
        # un nombre legítimo (.config/x.py → config/x.py).
        return text[2:] if text.startswith("./") else text

    normalized = [_norm(f) for f in files]
    for pattern in step.produces:
        p = _norm(_interpolate(pattern, state.vars, qpath, state.max_interp_chars))
        # fnmatch no da a `**/` el sentido de "cero o más directorios":
        # `src/**/*.py` no matcheaba `src/main.py`. Se prueban las variantes
        # con el `**/` colapsado.
        variants = {p}
        if "/**/" in p:
            variants.add(p.replace("/**/", "/"))
        if p.startswith("**/"):
            variants.add(p[3:])
        for variant in variants:
            for f in normalized:
                parts = f.split("/")
                for i in range(len(parts)):
                    suffix = "/".join(parts[i:])
                    if fnmatch.fnmatch(suffix, variant) or suffix.endswith("/" + variant):
                        return True
    return False


class EngineRuntime(Protocol):
    """Puerto de lo difuso. El motor jamás importa LLM/tools directamente."""

    async def call_agent(
        self,
        agent: str,
        prompt: str,
        *,
        model_role: str | None = None,
        step_id: str = "",
        path: str = "",
        max_iterations: int | None = None,
        extra_context: str = "",
        allowed_tools: list[str] | None = None,
    ) -> AgentOutcome: ...

    async def call_tool(self, tool: str, args: dict[str, Any]) -> dict[str, Any]: ...

    async def decompose(self, query: str, strategy: str) -> Any: ...

    async def decide(
        self, question: str, options: list[str], context: str, *, step_id: str = ""
    ) -> str: ...

    async def evaluate(self, question: str, expect: str, context: str) -> str: ...

    async def checkpoint(self, question: str, step_id: str = "") -> bool: ...

    async def aggregate(
        self, strategy: str, results: list[dict[str, Any]], agent: str | None = None
    ) -> str: ...

    async def commit_after_step(self, step_id: str) -> None: ...

    async def replan(
        self,
        *,
        goal: str,
        plan: list[dict[str, Any]],
        failed_item: dict[str, Any] | None,
        evidence: dict[str, Any],
        remaining_tokens: int,
        max_new_items: int,
        model_role: str = "reasoning",
    ) -> ReplanDecision:
        """Decisión del replanner; el motor valida y aplica con límites."""
        ...

    async def begin_budget_scope(self, budget_tokens: int) -> None:
        """Techo de presupuesto por-subtarea (min(techo, consumo+budget))."""
        ...

    async def end_budget_scope(self) -> None:
        """Restaura el techo vigente antes del scope."""
        ...

    def emit(self, **payload: Any) -> None: ...


@dataclass
class EngineResult:
    status: str  # "completed" | "failed" | "paused" | "stopped"
    outputs: dict[str, Any] = field(default_factory=dict)
    final_output: str | None = None
    vars: dict[str, Any] = field(default_factory=dict)
    failure: str | None = None
    errors: list[str] = field(default_factory=list)
    snapshot: dict[str, Any] | None = None
    run_state: dict[str, Any] | None = None
    stop_reason: str | None = None
    paused_question: str | None = None
    paused_options: list[str] = field(default_factory=list)
    paused_step_id: str | None = None
    paused_kind: str = "checkpoint"


_MAX_INTERP_CHARS = 2000


def _interpolate(
    text: str, vars_: dict[str, Any], where: str, max_chars: int = _MAX_INTERP_CHARS
) -> str:
    """Sustituye $var por su valor. Var indefinida → EngineError (fail-loud).

    El valor sustituido se acota (paridad con decide/evaluate): sin esto, un
    $last_output grande re-inyectado en el goal de cada iteración de un loop
    crecía el prompt de forma cuadrática y quemaba el presupuesto. El cap es
    por workflow: un preset que pasa un artefacto largo entre steps lo sube.
    """

    def _sub(match: re.Match[str]) -> str:
        var = match.group(1)
        if var not in vars_:
            raise EngineError(f"{where}: variable '${var}' sin valor en runtime")
        valor = str(vars_[var])
        if len(valor) > max_chars:
            logger.info(
                "%s: '$%s' truncado de %d a %d chars (max_interp_chars)",
                where,
                var,
                len(valor),
                max_chars,
            )
        return valor[:max_chars]

    return _VAR_REF_RE.sub(_sub, text)


def _eval_when(when: str | None, vars_: dict[str, Any], query: str) -> bool:
    if when is None:
        return True
    if when == "if_blockers":
        return bool(BLOCKER_RE.search(str(vars_.get("last_gate") or "")))
    if when.startswith("query_contains:"):
        token = when.split(":", 1)[1].lower()
        return token in str(query).lower()
    if when.startswith("!$"):
        # negación determinista: el step corre SOLO si la var es falsa
        # (p. ej. reparar el agregado solo cuando la verificación quedó roja).
        return not bool(vars_.get(when[2:]))
    if when.startswith("$"):
        return bool(vars_.get(when[1:]))
    # Bare var name también aceptado
    return bool(vars_.get(when))


def _metric_ok(value: float, op: str, target: float) -> bool:
    return {
        ">=": value >= target,
        ">": value > target,
        "<=": value <= target,
        "<": value < target,
        "==": value == target,
    }.get(op, False)


class _State:
    """Estado mutable de una ejecución (o reanudación)."""

    def __init__(
        self,
        vars_: dict[str, Any],
        resume: dict[str, Any] | None,
        max_interp_chars: int = _MAX_INTERP_CHARS,
        cycle_token_estimate: int = _CYCLE_TOKEN_ESTIMATE,
        run_state: RunState | None = None,
    ):
        self.vars = vars_
        self.max_interp_chars = max_interp_chars
        self.cycle_token_estimate = cycle_token_estimate
        self.run_state = run_state or RunState.from_dict((resume or {}).get("run_state"))
        self.completed: set[str] = set((resume or {}).get("completed") or [])
        self.loop_state: dict[str, int] = dict((resume or {}).get("loop_state") or {})
        self.children: dict[str, dict[str, Any]] = dict((resume or {}).get("children") or {})
        self.results: list[dict[str, Any]] = list(
            (resume or {}).get("results") or []
        )  # {id, output} en orden de ejecución
        self.errors: list[str] = []
        # veredictos en rojo del run (`step: var=False`): trazabilidad de la
        # verificación determinista que antes se calculaba y se descartaba.
        self.verdict_failures: list[str] = []
        # flag por-step: lo setea _tool y lo lee _exec_steps para emitir el
        # step como fallido en vez de completado (ver ToolStep.verdict).
        self.verdict_failed: bool = False
        # mediciones imposibles consecutivas por loop (until.metric): una
        # vuelta de gracia y luego fallo accionable — sin este límite el loop
        # podía agotar su presupuesto sin poder evaluar nunca su meta.
        self.until_unmeasured: dict[str, int] = {}
        # llamadas reales a call_agent por qpath: el "intento" del bloque de
        # memoria sobrevive al reintento de ciclo (retry_on_verification), que
        # re-entra al mismo step con el contador del `for` reiniciado en 1.
        # Viaja en el snapshot: un resume no vuelve a decir "intento 1".
        self.agent_attempts: dict[str, int] = dict((resume or {}).get("agent_attempts") or {})


_FILE_IN_GOAL_RE = re.compile(r"\b([A-Za-z0-9_./-]+\.(?:md|py|toml|txt|cfg|ini|json|yaml|yml))\b")


def _artefacto_entregado(goal: str, entregados: set[str]) -> str | None:
    """Archivo que el goal del descartado pedía y el run YA entregó.

    Evidencia para saltar sin mentir: un ítem "Completar README.md…" cuyo
    README ya escribió otra subtarea no es trabajo pendiente. Solo cuenta si
    el path aparece en los archivos del plan (no se inventa nada).
    """
    for match in _FILE_IN_GOAL_RE.finditer(goal or ""):
        path = match.group(1).lstrip("./")
        for entregado in entregados:
            if entregado == path or entregado.endswith("/" + path):
                return entregado
    return None


class WorkflowEngine:
    """Ejecuta un CompiledWorkflow contra un EngineRuntime."""

    def __init__(self, runtime: EngineRuntime):
        self._rt = runtime
        self._verified_by_map: dict[str, str] = {}

    async def run(
        self,
        cw: CompiledWorkflow,
        *,
        inputs: dict[str, Any] | None = None,
        resume: dict[str, Any] | None = None,
        default_budget: int = 0,
    ) -> EngineResult:
        dsl = cw.dsl
        vars_: dict[str, Any] = {"query": "", "item": "", "last_gate": "", "last_output": ""}
        if resume:
            vars_.update(resume.get("vars") or {})
        if inputs:
            vars_.update(inputs)
        for name in dsl.inputs:
            if name in dsl.defaults and name not in vars_:
                vars_[name] = dsl.defaults[name]

        state = _State(
            vars_,
            resume,
            max_interp_chars=dsl.max_interp_chars,
            cycle_token_estimate=dsl.cycle_token_estimate,
        )
        resume_state = RunState.from_dict((resume or {}).get("run_state"))
        if resume_state.run_id:
            state.run_state = resume_state
        else:
            state.run_state = RunState(
                run_id=uuid.uuid4().hex,
                goal=str(vars_.get("query") or ""),
                budget=BudgetState(
                    limit=int(dsl.budget_tokens or default_budget or 0),
                    reserve_final=int(dsl.budget_reserve_final or 0),
                    reserve_replan=int(dsl.budget_reserve_replan or 0),
                ),
            )
        self._seed_plan(cw, state)
        self._materialize_over_items(cw, state)
        self._reconcile_run_state(cw, state)
        self._verified_by_map = self._build_verified_by_map(cw)
        try:
            await self._exec_steps(cw, dsl.steps, state, prefix="")
        except _EnginePaused as p:
            result = self._result(cw, state, status="paused")
            result.paused_question = p.question or None
            result.paused_options = p.options
            result.paused_step_id = p.step_id or None
            result.paused_kind = p.kind
            if result.snapshot is not None:
                result.snapshot["paused_step"] = p.step_id
                result.snapshot["paused_kind"] = p.kind
            return result
        except _EngineFailed as e:
            return self._result(cw, state, status="failed", failure=e.message)
        except _EngineStopped as s:
            result = self._result(cw, state, status="stopped")
            result.stop_reason = s.reason
            return result
        return self._result(cw, state, status="completed")

    # ── Resultado y snapshot ───────────────────────────────────────────

    def _sync_budget(self, state: _State) -> None:
        """El gasto real del run incluye decompose/decide/aggregate y reintentos.

        El contador global del workflow es la fuente autoritativa; los
        `outcome.tokens` solo cubren los agent-steps. En tests/fakes el global
        es 0 y el máximo preserva la suma simulada.
        """
        try:
            from tools.orchestrator import get_llm_token_usage

            used = int(get_llm_token_usage() or 0)
        except Exception:
            return
        if used > state.run_state.budget.spent:
            state.run_state.budget.spent = used

    def _result(
        self,
        cw: CompiledWorkflow,
        state: _State,
        *,
        status: str,
        failure: str | None = None,
    ) -> EngineResult:
        self._sync_budget(state)
        outputs = {name: state.vars.get(name) for name in cw.dsl.outputs}
        final = None
        if status == "completed":
            final = state.vars.get("last_output")
        snapshot = None
        if status == "paused":
            snapshot = {
                "vars": state.vars,
                "completed": sorted(state.completed),
                "loop_state": state.loop_state,
                "children": state.children,
                "run_state": state.run_state.to_dict(),
                "agent_attempts": dict(state.agent_attempts),
            }
        return EngineResult(
            status=status,
            outputs=outputs,
            final_output=final,
            vars=state.vars,
            failure=failure,
            errors=state.errors,
            snapshot=snapshot,
            run_state=state.run_state.to_dict(),
        )

    # ── Plan del run ───────────────────────────────────────────────────

    def _seed_plan(self, cw: CompiledWorkflow, state: _State) -> None:
        """Siembra las fases de primer nivel; las subtasks llegan con su `over`."""
        if state.run_state.plan:
            return
        for step in cw.dsl.steps:
            if isinstance(step, LoopStep) and step.over:
                state.run_state.plan.append(
                    PlanItem(kind="phase", id=step.id, goal=step.id, path=step.id)
                )
            elif isinstance(step, LoopStep):
                state.run_state.plan.append(
                    PlanItem(kind="subtask", id=step.id, goal=step.id, path=step.id)
                )
            else:
                goal = str(getattr(step, "goal", "") or step.id)
                state.run_state.plan.append(
                    PlanItem(kind="phase", id=step.id, goal=goal, path=step.id)
                )

    def _materialize_over_items(self, cw: CompiledWorkflow, state: _State) -> None:
        """Crea los subtask de un loop over cuando su variable ya existe.

        Se llama después de cada step: `parse_historias`/`decompose` dejan la
        lista, y `revisar_historias` (antes del loop) ya puede verla.
        """
        for step in cw.dsl.steps:
            if not isinstance(step, LoopStep) or not step.over:
                continue
            raw = state.vars.get(step.over)
            if raw is None:
                continue
            items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
            existing = {
                p.path for p in state.run_state.plan if p.path and p.path.startswith(f"{step.id}#")
            }
            for idx, item in enumerate(items, 1):
                path = f"{step.id}#{idx}"
                if path in existing:
                    continue
                state.run_state.plan.append(
                    PlanItem(kind="subtask", id=path, goal=str(item), path=path)
                )
            # Reconciliación: si la lista encoge (filtrar_items, replace del
            # replan), los ítems que ya no existen NO quedan pending — un plan
            # que reporta trabajo pendiente que el motor descartó miente.
            # Además los goals se re-sincronizan con la lista VIGENTE: el plan
            # mapea por índice y un filtro que descarta un ítem del medio
            # dejaba al ítem entregado con el goal del descartado.
            dropped = list(state.vars.get(f"_dropped_{step.over}") or [])
            contrato = bool(state.vars.get(f"_dropped_contrato_{step.over}"))
            entregados = {f for p in state.run_state.plan for f in p.files_written}
            for p in state.run_state.plan:
                if p.path and p.path.startswith(f"{step.id}#") and p.kind == "subtask":
                    try:
                        idx = int(p.path.rsplit("#", 1)[1])
                    except ValueError:
                        continue
                    if idx <= len(items):
                        p.goal = str(items[idx - 1])
                    elif p.status in ("pending", "running") or (
                        p.status == "skipped" and not p.satisfied_by
                    ):
                        # también se reevalúa un skipped previo: el filtro
                        # corre ANTES del loop y la evidencia (archivo ya
                        # entregado) puede aparecer después.
                        p.status = "skipped"
                        # el saltado muestra el descartado REAL cuando el
                        # plugin lo registró; si no, la nota genérica.
                        drop_idx = idx - len(items) - 1
                        goal_descartado = (
                            str(dropped[drop_idx]) if 0 <= drop_idx < len(dropped) else ""
                        )
                        if goal_descartado:
                            p.goal = goal_descartado
                        # evidencia: si el artefacto que pedía el descartado
                        # ya lo entregó el run, el skip está satisfecho (un
                        # README escrito por otra subtarea no es trabajo
                        # pendiente).
                        evidencia = _artefacto_entregado(goal_descartado, entregados)
                        if evidencia:
                            p.satisfied_by = evidencia
                            note = f"artefacto ya entregado por el run ({evidencia})"
                        elif contrato:
                            note = "descartado por contrato del preset (no ejecutado)"
                        else:
                            note = "descartado antes del loop (filtro)"
                        if note not in p.notes:
                            p.notes.append(note)

    def _reconcile_run_state(self, cw: CompiledWorkflow, state: _State) -> None:
        """Resume: un item interrumpido vuelve a pending; las fases ya hechas
        se reconocen por `completed`."""
        for item in state.run_state.plan:
            if item.status == "running":
                item.status = "pending"
            if (
                item.kind == "phase"
                and item.path in state.completed
                and item.status in ("pending", "running")
            ):
                item.status = "delivered"

    def _build_verified_by_map(self, cw: CompiledWorkflow) -> dict[str, str]:
        """tool_id → agent_id del contrato `verified_by` (ids únicos)."""
        mapping: dict[str, str] = {}

        def walk(steps: list[Any]) -> None:
            for step in steps:
                if isinstance(step, AgentStep) and step.verified_by:
                    mapping[step.verified_by] = step.id
                if isinstance(step, LoopStep):
                    walk(step.body)
                elif isinstance(step, ParallelStep):
                    for branch in step.branches:
                        walk(branch.steps)
                elif isinstance(step, DecideStep):
                    for branch_steps in step.branches.values():
                        walk(branch_steps)

        walk(cw.dsl.steps)
        return mapping

    def _cycle_estimate(self, state: _State, step: LoopStep) -> int:
        """Costo estimado del próximo ciclo: max(piso, media observada).

        El piso global de 8k dejaba arrancar ciclos de 100k+ con 54k de
        holgura y el cap global cortaba el ciclo a mitad. Los ciclos ya
        ejecutados del MISMO loop son la mejor predicción disponible.
        """
        floor = int(getattr(state, "cycle_token_estimate", _CYCLE_TOKEN_ESTIMATE))
        costs = [
            p.tokens
            for p in state.run_state.plan
            if p.kind == "subtask" and p.path and p.path.startswith(f"{step.id}#") and p.tokens > 0
        ]
        if not costs:
            return floor
        return max(floor, int(sum(costs) / len(costs)))

    async def _budget_exhausted(
        self,
        state: _State,
        step: LoopStep,
        items: list[Any] | None,
        iteration: int,
    ) -> bool:
        """Gate pre-ciclo: si el trabajo disponible no alcanza, no arranca.

        Antes de bloquear, si el loop declara el trigger `budget`, el
        replanner puede reducir el alcance (replace/skip) o parar; la reserva
        final no se toca.
        """
        self._sync_budget(state)
        budget = state.run_state.budget
        if not budget.limit or budget.available_for_work() >= self._cycle_estimate(state, step):
            return False
        policy = step.replan
        if (
            policy is not None
            and policy.max > 0
            and "budget" in policy.triggers
            and len(state.run_state.replans) < policy.max
        ):
            decision, tokens = await self._request_replan(state, step, None, trigger="budget")
            before = [p.goal for p in state.run_state.plan if p.kind == "subtask"]
            applied = self._apply_replan(state, step, None, decision, iteration)
            after = [p.goal for p in state.run_state.plan if p.kind == "subtask"]
            state.run_state.replans.append(
                ReplanRecord(
                    at=datetime.datetime.now(datetime.UTC).isoformat(),
                    trigger="budget",
                    reason=decision.reason,
                    action=applied,
                    before=before,
                    after=after,
                    tokens=tokens,
                )
            )
            if applied == "replace":
                if items is not None:
                    items[:] = items[:iteration] + [new.goal for new in decision.items]
                    if step.over:
                        state.vars[step.over] = list(items)
                return False
            if applied == "skip":
                return False
        self._block_pending(state, step, items, iteration, "budget")
        self._rt.emit(
            kind="budget_exhausted",
            path=step.id,
            status="blocked",
            run_state=state.run_state.public(),
        )
        return True

    def _block_pending(
        self,
        state: _State,
        step: LoopStep,
        items: list[Any] | None,
        iteration: int,
        reason: str,
    ) -> None:
        if items is not None:
            for idx in range(iteration, len(items)):
                item = state.run_state.find(path=f"{step.id}#{idx + 1}")
                if item is not None and item.status not in ("delivered", "skipped"):
                    item.status = "blocked"
                    item.last_error = reason
        else:
            item = state.run_state.find(item_id=step.id)
            if item is not None:
                item.status = "blocked"
                item.last_error = reason

    # ── Secuencia ──────────────────────────────────────────────────────

    def _plan_item_for_step(self, state: _State, qpath: str) -> PlanItem | None:
        """PlanItem de un step TOP-LEVEL; los steps dentro de un loop se
        reflejan en la subtarea del loop, no en un item propio."""
        if "/" in qpath:
            return None
        return state.run_state.find(path=qpath)

    def _mark_plan_item(
        self,
        state: _State,
        qpath: str,
        status: str,
        error: str | None = None,
        *,
        nested: bool = False,
    ) -> None:
        """Marca el item del step si sigue en un estado no final.

        Los pasos con evidencia propia (agente/tool/loop) ya fijaron su
        estado; esto cubre plugin/decompose/aggregate/decide/include y los
        caminos de skip/fallo, que antes dejaban el plan en `pending` o
        `running` para siempre. Con ``nested=True`` un fallo dentro de un
        loop marca la SUBTAREA del ciclo (el body no tiene item propio).
        """
        item = self._plan_item_for_step(state, qpath)
        if item is None and nested:
            item = state.run_state.find_for_path(qpath)
        if item is None or item.status not in ("pending", "running", "skipped"):
            return
        item.status = status
        if error:
            item.last_error = error

    def _persist_progress(self, state: _State, step_no: int) -> None:
        """Journal parcial best-effort tras un step de nivel superior."""
        persist = getattr(self._rt, "persist_run", None)
        if not callable(persist):
            return
        try:
            persist(state.run_state, step_no)
        except Exception:
            logger.debug("journal parcial no escrito", exc_info=True)

    async def _exec_steps(
        self,
        cw: CompiledWorkflow,
        steps: list[StepSpec],
        state: _State,
        *,
        prefix: str,
    ) -> None:
        for step_no, step in enumerate(steps, 1):
            qpath = f"{prefix}{step.id}"
            if qpath in state.completed:
                continue
            if not _eval_when(step.when, state.vars, str(state.vars.get("query") or "")):
                item = self._plan_item_for_step(state, qpath)
                if item is not None and item.status in ("pending", "running", "skipped"):
                    item.status = "skipped"
                    note = f"when falso: {step.when}"
                    if note not in item.notes:
                        item.notes.append(note)
                continue
            item = self._plan_item_for_step(state, qpath)
            if item is not None and item.status in ("pending", "skipped"):
                item.status = "running"
            state.verdict_failed = False
            self._rt.emit(
                kind="step",
                path=qpath,
                status="running",
                run_state=state.run_state.public(),
            )
            try:
                await self._exec_step(cw, step, state, qpath=qpath, prefix=prefix)
            except _EnginePaused as p:
                if not p.step_id:
                    p.step_id = step.id
                raise
            except _EngineFailed as e:
                self._mark_plan_item(state, qpath, "failed", str(e), nested=True)
                raise  # señal de control de flujo, NO error de step
            except _EngineStopped as e:
                self._mark_plan_item(state, qpath, "blocked", str(e), nested=True)
                raise  # stop terminal: NO es un fallo técnico del step
            except PauseRequested as pr:
                # un adapter pide pausa humana (clarificación/aprobación);
                # lleva el step: el resume inyecta la respuesta ahí
                raise _EnginePaused(pr.question, pr.options, kind=pr.kind, step_id=step.id) from pr
            except Exception as e:
                if step.on_error == "continue":
                    self._mark_plan_item(state, qpath, "failed", str(e), nested=True)
                    state.errors.append(f"{qpath}: {e}")
                    logger.warning("step %s falló (on_error=continue): %s", qpath, e)
                    self._rt.emit(
                        kind="step",
                        path=qpath,
                        status="error",
                        run_state=state.run_state.public(),
                    )
                    continue
                self._mark_plan_item(state, qpath, "failed", str(e), nested=True)
                raise _EngineFailed(f"{qpath}: {e}") from e
            if isinstance(step, AgentStep) and step.on_blocker == "stop" and step.gate:
                output = str(state.vars.get("last_output") or "")
                pattern = step.gate if isinstance(step.gate, str) else BLOCKER_RE.pattern
                if re.search(pattern, output):
                    raise _EngineStopped(output[:500] or f"blocker en {step.id}")
            self._mark_plan_item(
                state,
                qpath,
                "failed" if state.verdict_failed else "delivered",
            )
            state.completed.add(qpath)
            self._materialize_over_items(cw, state)
            if not prefix:
                self._persist_progress(state, step_no)
            # el step corrió, pero su veredicto puede haberlo reprobado: se
            # emite 'error' para que el conteo (completadas/fallidas) y la GUI
            # digan la verdad — antes un rojo de verificación quedaba invisible.
            self._rt.emit(
                kind="step",
                path=qpath,
                status="error" if state.verdict_failed else "completed",
                run_state=state.run_state.public(),
            )
            # commit_after: decisión declarada del workflow —
            # el MOTOR committea tras completar steps con id declarado.
            # Resume no re-committea: el completed-check de arriba salta
            # steps ya terminados antes de llegar aquí.
            # Un veredicto en rojo NO committea: commitear una entrega
            # reprobada contradice el contrato ("solo con subtarea completada").
            if step.id in cw.dsl.commit_after and not state.verdict_failed:
                await self._rt.commit_after_step(step.id)

    async def _exec_step(
        self,
        cw: CompiledWorkflow,
        step: StepSpec,
        state: _State,
        *,
        qpath: str,
        prefix: str,
    ) -> None:
        if isinstance(step, AgentStep):
            await self._agent(step, state, qpath)
        elif isinstance(step, ToolStep):
            await self._tool(step, state, qpath)
        elif isinstance(step, DecomposeStep):
            await self._decompose(step, state, qpath)
        elif isinstance(step, LoopStep):
            await self._loop(cw, step, state, qpath=qpath, prefix=prefix)
        elif isinstance(step, ParallelStep):
            await self._parallel(cw, step, state, prefix=prefix)
        elif isinstance(step, DecideStep):
            await self._decide(cw, step, state, qpath=qpath, prefix=prefix)
        elif isinstance(step, CheckpointStep):
            await self._checkpoint(step, state, qpath)
        elif isinstance(step, WorkflowStep):
            await self._include(cw, step, state, qpath=qpath)
        elif isinstance(step, AggregateStep):
            await self._aggregate(step, state, qpath)
        elif isinstance(step, PluginStep):
            await self._plugin(step, state, qpath)
        else:
            raise EngineError(f"{qpath}: kind no soportado por el motor: {type(step).__name__}")

    async def _plugin(self, step: Any, state: _State, qpath: str) -> None:
        from orchestration.dsl.registry import get_primitive, known_primitives

        executor = get_primitive(step.plugin)
        if executor is None:
            raise EngineError(
                f"{qpath}: primitiva plugin '{step.plugin}' no registrada "
                f"(disponibles: {', '.join(known_primitives())})"
            )
        await executor(step, state, self._rt, qpath)

    # ── Steps concretos ────────────────────────────────────────────────

    async def _agent(self, step: AgentStep, state: _State, qpath: str) -> None:
        prompt = _interpolate(step.goal or "", state.vars, qpath, state.max_interp_chars)
        # El plan se siembra con el goal CRUDO ("Revisa $query"): el agente y el
        # reporte leen un placeholder. Para fases, guardamos la versión
        # interpolada (acotada) — las subtasks ya llevan su ítem concreto.
        plan_item = state.run_state.find_for_path(qpath)
        if (
            plan_item is not None
            and plan_item.kind == "phase"
            # solo el item del PROPIO step: un paso anidado no renombra la
            # fase padre con su prompt interno.
            and plan_item.goal == str(step.goal or "")
            and "$" in (plan_item.goal or "")
        ):
            plan_item.goal = truncar_en_palabra(prompt, 300)
        retry_hint = str(state.vars.pop("_retry_hint", "") or "")
        if retry_hint:
            prompt = f"{prompt}\n\n{retry_hint}"
        attempts = step.retry_max + 1
        output: str | None = None
        raw_output: Any = None
        outcome: AgentOutcome | None = None
        last_error: Exception | None = None
        reserve_used = 0
        for attempt in range(1, attempts + 1):
            extra = 0
            tokens_attempt_before = 0
            if step.use_reserve_final:
                from tools.orchestrator import get_llm_token_usage

                tokens_attempt_before = get_llm_token_usage()
                extra = max(
                    0,
                    int(state.run_state.budget.reserve_final or 0)
                    - int(state.run_state.budget.reserve_final_spent or 0),
                )
                if extra > 0:
                    from tools.orchestrator import push_budget_extra

                    push_budget_extra(extra)
            outcome = None
            try:
                # Intento REAL por qpath: el reintento de ciclo re-entra al step
                # con el `for` reiniciado; el contador acumulado evita que dos
                # ejecuciones del mismo subtask digan "intento 1".
                state.agent_attempts[qpath] = state.agent_attempts.get(qpath, 0) + 1
                # Timeout por intento: una llamada LLM colgada no puede
                # congelar el paso (y con él el run) indefinidamente.
                agent_kwargs: dict[str, Any] = {
                    "model_role": step.model_role,
                    "step_id": step.id,
                    "path": qpath,
                    # Memoria del run: el agente no comparte historial entre
                    # steps, así que el bloque de estado es el único puente
                    # (plan, archivos, verificación, intentos y budget).
                    "extra_context": state.run_state.render_memory_block(
                        current_path=qpath, attempt=state.agent_attempts[qpath]
                    ),
                }
                if step.max_iterations:
                    agent_kwargs["max_iterations"] = step.max_iterations
                if step.tools is not None:
                    agent_kwargs["allowed_tools"] = list(step.tools)
                outcome = _as_outcome(
                    await asyncio.wait_for(
                        self._rt.call_agent(step.agent, prompt, **agent_kwargs),
                        timeout=step.timeout,
                    )
                )
                output = outcome.text
                raw_output = outcome.raw if outcome.raw is not None else output
                last_error = None
                if not str(output or "").strip() and attempt < attempts:
                    # salida vacía = contrato incumplido (el paso mudo que
                    # mataba runs enteros): reintentar ANTES de degradar —
                    # el reintento que sigue es barato frente al run perdido
                    logger.warning(
                        "agent %s intento %d/%d: salida vacía — reintentando",
                        qpath,
                        attempt,
                        attempts,
                    )
                    continue
                break
            except (_EnginePaused, PauseRequested):
                raise  # la pausa humana jamás es objeto de retry
            except Exception as e:
                last_error = e
                if attempt < attempts:
                    # TimeoutError llega SIN mensaje — `()` confundía el
                    # diagnóstico (caso real: 'intentó 1/3 falló ()').
                    err_txt = str(e).strip()
                    desc = f"{type(e).__name__}: {err_txt}" if err_txt else type(e).__name__
                    logger.warning(
                        "agent %s intento %d/%d falló (%s) — reintentando",
                        qpath,
                        attempt,
                        attempts,
                        desc,
                    )
            finally:
                if step.use_reserve_final:
                    from tools.orchestrator import get_llm_token_usage

                    used = max(0, get_llm_token_usage() - tokens_attempt_before)
                    reserve_used += min(
                        extra,
                        used or int((outcome.tokens if outcome is not None else 0) or 0),
                    )
                if extra > 0:
                    from tools.orchestrator import pop_budget_ceiling

                    pop_budget_ceiling()
        if last_error is not None:
            if isinstance(last_error, TimeoutError):
                raise EngineError(
                    f"{qpath}: el agente excedió {step.timeout}s sin responder "
                    f"en {attempts} intento(s)"
                ) from last_error
            raise last_error
        assert output is not None
        if not str(output).strip():
            # agotó los intentos SIN producir nada: fallar el paso AQUÍ —
            # continuar con "" solo quema los steps siguientes hasta que
            # el until descubre el hueco dos pasos después.
            raise EngineError(
                f"{qpath}: salida vacía tras {attempts} intento(s) — el paso "
                "no produjo texto ni archivos"
            )
        state.vars["last_output"] = raw_output
        for var in step.outputs:
            state.vars[var] = raw_output
        # id = camino cualificado: las iteraciones de un loop comparten
        # step.id y colapsaban el dict del agregador (N subtareas → 1 sola
        # clave, summary con "Subtarea subtarea"). La task es el texto del
        # $item cuando el paso corre DENTRO de un loop over (fuera del loop
        # el item queda stale y no debe atribuirse).
        task = str(state.vars.get("item") or "")[:120] if "#" in qpath else ""
        state.results.append(
            {
                "id": qpath,
                "output": raw_output,
                "status": outcome.status if outcome is not None else "completed",
                "task": task,
            }
        )
        if step.use_reserve_final:
            state.run_state.budget.reserve_final_spent += min(
                int(state.run_state.budget.reserve_final or 0), reserve_used
            )
        if outcome is not None:
            state.run_state.budget.spent += int(outcome.tokens or 0)
        item = state.run_state.find_for_path(qpath)
        if item is not None and (item.kind == "subtask" or item.path == qpath):
            # El dueño de un step anidado es la subtarea del CICLO (loop con
            # over): su entrega la decide el step del ciclo. Un loop SIN over
            # no tiene ciclos: el dueño es el propio loop y su estado lo
            # gobierna _loop (running hasta la última ronda). Sin este filtro,
            # el primer agent-step de la ronda 1 marcaba el loop "delivered"
            # y la GUI lo mostraba entregado con rondas pendientes.
            parent_path = qpath.rsplit("/", 1)[0] if "/" in qpath else ""
            owns_status = item.path == qpath or (bool(parent_path) and item.path == parent_path)
            item.attempts += 1
            if outcome is not None:
                item.tokens += int(outcome.tokens or 0)
                item.iterations += int(outcome.iterations or 0)
                for f in outcome.files_written:
                    if f not in item.files_written:
                        item.files_written.append(f)
                if outcome.status == "completed":
                    if step.produces and not _produces_ok(
                        step, state, outcome.files_written, qpath
                    ):
                        if owns_status:
                            item.status = "unmeasured"
                        item.last_error = "faltan artefactos: " + ", ".join(
                            _interpolate(p, state.vars, qpath, state.max_interp_chars)
                            for p in step.produces
                        )
                    elif step.min_output_chars and len(str(output).strip()) < step.min_output_chars:
                        if owns_status:
                            item.status = "unmeasured"
                        item.last_error = (
                            f"salida corta: {len(str(output).strip())} < {step.min_output_chars}"
                        )
                    elif step.verified_by:
                        # espera la medición del ToolStep del ciclo
                        if owns_status:
                            item.status = "unmeasured"
                    elif owns_status:
                        item.status = "delivered"
                elif outcome.status in ("stalled", "failed"):
                    if owns_status:
                        item.status = "unmeasured"
                    item.last_error = outcome.error or f"agente {outcome.status}"
            # El status del RESULTADO es el de la entrega del ciclo (over). En
            # un loop ciego el dueño es el loop entero (sigue "running"): el
            # resultado conserva el status del agent call (completed) para que
            # el agregador no cuente 0 entregas a mitad del debate.
            if owns_status and state.results and state.results[-1].get("id") == qpath:
                state.results[-1]["status"] = item.status
        if step.gate:
            # Semántica pipeline: el stage-gate publica su salida completa;
            # `when: if_blockers` la evalúa con BLOCKER_RE.
            state.vars["last_gate"] = output
            if isinstance(step.gate, str) and re.search(step.gate, output):
                state.vars[f"gate_{step.id}"] = True

    async def _tool(self, step: ToolStep, state: _State, qpath: str) -> None:
        args = {}
        for k, v in step.args.items():
            # Un arg que ES exactamente una referencia ($var) pasa el VALOR
            # CRUDO, no su str(): así una lista (p.ej. los artefactos del run
            # para la verificación del agregado) llega como lista a la tool.
            m = _VAR_SOLO_RE.match(v.strip()) if isinstance(v, str) else None
            if m and m.group(1) in state.vars:
                args[k] = state.vars[m.group(1)]
            elif isinstance(v, str):
                args[k] = _interpolate(v, state.vars, qpath, state.max_interp_chars)
            else:
                args[k] = v
        result = await self._rt.call_tool(step.tool, args)
        state.vars["last_output"] = result
        for var, key in step.outputs.items():
            state.vars[var] = result.get(key) if isinstance(result, dict) else result
        # Un tool-step de solo medición (sin `verdict`) que publica un bool
        # midió: un rojo no es "sin medición". El gate final es el que
        # reprueba la entrega; el intermedio solo deja evidencia.
        measured: bool | None = None
        if not step.verdict and isinstance(result, dict):
            has_counts = any(
                key in result
                for key in ("passed_count", "failed_count", "error_count", "total_count")
            )
            if has_counts:
                for var in step.outputs:
                    val = state.vars.get(var)
                    if isinstance(val, bool):
                        measured = val
                        break
        # Solo un veredicto MEDIDO en False reprueba: `None` significa que la
        # verificación no corrió (p.ej. contrato TEST_PATH incumplido) y eso ya
        # lo marca la degradación del adapter — contarlo además como fallo
        # sobre-reportaba entregas que nadie llegó a evaluar.
        if step.verdict:
            verdict = state.vars.get(step.verdict)
            if verdict is False:
                self._marcar_veredicto_en_rojo(step, state, qpath)
            elif verdict is True:
                self._marcar_veredicto_en_verde(qpath)
        item = state.run_state.find_for_path(qpath)
        if item is not None and item.kind == "phase" and item.path == qpath:
            verdict = state.vars.get(step.verdict) if step.verdict else None
            if step.verdict:
                counts = {
                    key: result.get(key)
                    for key in ("passed_count", "failed_count", "error_count", "total_count")
                    if isinstance(result, dict) and key in result
                }
                item.verification = Verification(
                    tool=step.tool,
                    args=dict(args),
                    passed=verdict if isinstance(verdict, bool) else None,
                    counts=counts,
                    output_tail=str(result.get("output") or "")[:500],
                )
            if verdict is False or state.verdict_failed:
                item.status = "failed"
                item.last_error = f"verificación en rojo: {step.verdict}=False"
            elif verdict is True:
                item.status = "delivered"
                item.last_error = None
            elif step.verdict:
                item.status = "unmeasured"
                item.last_error = "sin medición"
            elif measured is not None:
                counts = {
                    key: result.get(key)
                    for key in ("passed_count", "failed_count", "error_count", "total_count")
                    if isinstance(result, dict) and key in result
                }
                item.verification = Verification(
                    tool=step.tool,
                    args=dict(args),
                    passed=measured,
                    counts=counts,
                    output_tail=str(result.get("output") or "")[:500],
                )
                item.status = "delivered"
                item.last_error = None
            else:
                item.status = "delivered" if result.get("success", True) else "unmeasured"
        if item is not None:
            # evidencia de "qué se intentó": el journal la guarda y
            # render_memory_block la inyecta como "Already tried" en el
            # prompt de los steps siguientes.
            state.run_state.memory.record_tool(
                item.id,
                f"{step.tool}|{args.get('file_path') or args.get('file_paths') or ''}",
            )
        agent_id = self._verified_by_map.get(step.id)
        if agent_id and step.verdict:
            prefix = qpath.rsplit("/", 1)[0] if "/" in qpath else ""
            target = state.run_state.find(path=prefix) if prefix else None
            if target is None:
                # fallback por id del agente (defensivo: el validador rechaza
                # produces/verified_by dentro de parallel/decide).
                target = state.run_state.find(item_id=agent_id)
            if target is not None:
                verdict = state.vars.get(step.verdict)
                counts = {
                    key: result.get(key)
                    for key in ("passed_count", "failed_count", "error_count", "total_count")
                    if isinstance(result, dict) and key in result
                }
                target.verification = Verification(
                    tool=step.tool,
                    args=dict(args),
                    passed=verdict if isinstance(verdict, bool) else None,
                    counts=counts,
                    output_tail=str(result.get("output") or "")[:500],
                )
                if verdict is True:
                    target.status = "delivered"
                    target.last_error = None
                elif verdict is False:
                    target.status = "failed"
                    target.last_error = f"verificación en rojo: {step.verdict}=False"
                else:
                    target.status = "unmeasured"
                agent_qpath = f"{prefix}/{agent_id}" if prefix else agent_id
                for r in state.results:
                    if r.get("id") == agent_qpath:
                        r["status"] = target.status

    def _marcar_veredicto_en_rojo(self, step: ToolStep, state: _State, qpath: str) -> None:
        """Un veredicto medido en False reprueba el step y la entrega de su iteración.

        La entrega del ciclo vive en ``state.results`` con el MISMO prefijo de
        camino cualificado (``loop#N/''), así que se marca ahí: sin esto el
        agregador y el scorecard la seguían contando como completada (el caso
        medido: development con ``test_runner: 1 failures`` y 18/18 completadas).
        """
        state.verdict_failed = True
        state.verdict_failures.append(f"{qpath}: {step.verdict}=False")
        # canal existente hacia EngineResult.errors: el rojo de verificación
        # queda auditable en el resultado del run, no solo en el log.
        state.errors.append(f"{qpath}: verificación en rojo ({step.verdict}=False)")
        prefijo = f"{qpath.rsplit('/', 1)[0]}/" if "/" in qpath else ""
        marcadas = 0
        if prefijo:
            for r in state.results:
                if str(r.get("id") or "").startswith(prefijo) and r.get("status") in (
                    "completed",
                    "delivered",
                ):
                    r["status"] = "failed"
                    marcadas += 1
            item = state.run_state.find(path=prefijo.rstrip("/"))
            if item is not None:
                item.status = "failed"
                item.last_error = f"verificación en rojo: {step.verdict}=False"
        logger.warning(
            "verificación en rojo en %s (%s=False): %d entrega(s) del ciclo "
            "marcadas como fallidas",
            qpath,
            step.verdict,
            marcadas,
        )

    def _marcar_veredicto_en_verde(self, qpath: str) -> None:
        """La verificación del ciclo pasó: sus stalls quedan recuperados.

        Un agent-step cortado por el cap de iteraciones pero cuya verificación
        determinista pasa entregó lo que debía: cuenta como recuperado, no
        como degradado. El adapter decide qué entradas del prefijo
        ``loop#N/`` retira; sin runtime con recovery es un no-op.
        """
        prefijo = f"{qpath.rsplit('/', 1)[0]}/" if "/" in qpath else ""
        if not prefijo:
            return
        recover = getattr(self._rt, "recover_steps", None)
        if callable(recover):
            recover(prefijo)

    def _mark_degraded(self, label: str, path: str) -> None:
        """Registra una degradación con su path (recuperable o no).

        El adapter guarda label+path para poder retirar el stall cuando la
        verificación del ciclo pasa; los runtimes sin ese contrato conservan
        el append clásico a `_degraded_steps`.
        """
        mark = getattr(self._rt, "mark_degraded", None)
        if callable(mark):
            mark(label, path)
            return
        degraded = getattr(self._rt, "_degraded_steps", None)
        if degraded is not None:
            degraded.append(label)

    async def _decompose(self, step: DecomposeStep, state: _State, qpath: str) -> None:
        query = (
            _interpolate(step.query, state.vars, qpath, state.max_interp_chars)
            if step.query
            else str(state.vars.get("query") or "")
        )
        result = await self._rt.decompose(query, step.strategy)
        if step.max_items and isinstance(result, list) and len(result) > step.max_items:
            # El cap es POR CONTRATO del preset (presupuesto compartido): el
            # plan se trunca y queda VISIBLE en la nota, pero no degrada el
            # proceso — el modelo sobre-generó, el run no dejó trabajo a medias.
            descartadas = len(result) - step.max_items
            result = result[: step.max_items]
            logger.warning(
                "%s: plan truncado por max_items: %d ítem(s) descartados",
                qpath,
                descartadas,
            )
            item = self._plan_item_for_step(state, qpath)
            if item is not None:
                note = f"plan truncado por max_items: {descartadas} ítem(s) descartados"
                if note not in item.notes:
                    item.notes.append(note)
        state.vars[step.output] = result
        state.vars["last_output"] = result

    async def _checkpoint(self, step: CheckpointStep, state: _State, qpath: str) -> None:
        question = _interpolate(step.question, state.vars, qpath, state.max_interp_chars)
        approved = await self._rt.checkpoint(question, step_id=step.id)
        if not approved:
            raise _EnginePaused(question)

    async def _aggregate(self, step: AggregateStep, state: _State, qpath: str) -> None:
        summary = await self._rt.aggregate(step.strategy, list(state.results), agent=step.agent)
        state.vars["last_output"] = summary

    async def _decide(
        self,
        cw: CompiledWorkflow,
        step: DecideStep,
        state: _State,
        *,
        qpath: str,
        prefix: str,
    ) -> None:
        question = _interpolate(step.question, state.vars, qpath, state.max_interp_chars)
        context = str(state.vars.get("last_output") or "")[: state.max_interp_chars]
        answer = await self._rt.decide(question, step.options, context, step_id=step.id)
        # Matching tolerante (paridad until-agent): el modelo responde en
        # prosa ("No: porque...") — exacto o primer token case-insensitive;
        # se normaliza a la opción ORIGINAL para el dispatch de branches.
        opciones_norm = {str(o).strip().upper(): o for o in step.options}
        elegida = opciones_norm.get(str(answer).strip().upper())
        if elegida is None:
            normalized = str(answer).strip().upper()
            first_token = re.split(r"[\s:;,.\-—¡!¿?]+", normalized)[0] if normalized else ""
            elegida = opciones_norm.get(first_token)
        if elegida is not None:
            answer = elegida
        else:
            logger.warning(
                "decide %s: respuesta inválida %r → fallback %r",
                qpath,
                answer,
                step.fallback,
            )
            answer = step.fallback
        inner_prefix = f"{prefix}{step.id}/{answer}/"
        await self._exec_steps(cw, step.branches[answer], state, prefix=inner_prefix)

    async def _include(
        self, cw: CompiledWorkflow, step: WorkflowStep, state: _State, *, qpath: str
    ) -> None:
        child_cw = cw.includes.get(step.name)
        if child_cw is None:
            raise EngineError(f"{qpath}: include '{step.name}' no resuelto")
        child_inputs = {
            target: (
                _interpolate(source, state.vars, qpath, state.max_interp_chars)
                if isinstance(source, str) and source.startswith("$")
                else source
            )
            for target, source in step.inputs.items()
        }
        child_resume = state.children.get(step.id)
        child = WorkflowEngine(self._rt)
        child_result = await child.run(child_cw, inputs=child_inputs, resume=child_resume)
        if child_result.status == "failed":
            raise _EngineFailed(f"{qpath}: include '{step.name}' falló: {child_result.failure}")
        if child_result.status == "stopped":
            raise _EngineStopped(child_result.stop_reason or f"include '{step.name}' detenido")
        if child_result.status == "paused":
            state.children[step.id] = child_result.snapshot or {}
            raise _EnginePaused(
                child_result.paused_question or f"include '{step.name}' pausado",
                child_result.paused_options,
            )
        state.children.pop(step.id, None)
        for out in step.outputs:
            state.vars[out] = child_result.vars.get(out)
        if child_result.final_output is not None:
            state.vars["last_output"] = child_result.final_output

    # ── Loop ───────────────────────────────────────────────────────────

    async def _loop(
        self,
        cw: CompiledWorkflow,
        step: LoopStep,
        state: _State,
        *,
        qpath: str,
        prefix: str,
    ) -> None:
        if step.parallel:
            # body concurrente por ítem (validator ya exige over y sin until)
            await self._loop_parallel(cw, step, state, qpath=qpath, prefix=prefix)
            return
        items: list[Any] | None = None
        if step.over:
            raw = state.vars.get(step.over)
            if raw is None:
                raise EngineError(f"{qpath}: loop.over '${step.over}' sin valor")
            items = list(raw) if isinstance(raw, (list, tuple)) else [raw]

        for var, value in step.init_vars.items():
            state.vars.setdefault(var, value)

        done = state.loop_state.get(qpath, 0)
        iteration = done
        until_hit = False
        loop_item = state.run_state.find(item_id=step.id)
        if loop_item is not None:
            loop_item.status = "running"
        retries_by_cycle: dict[str, int] = {}
        stopped_by_replan = False
        budget_blocked = False
        while True:
            if items is not None and iteration >= len(items):
                break
            if items is None and iteration >= step.max_iter:
                break
            if items is not None and iteration >= step.max_iter:
                break  # tope duro incluso iterando lista
            skipped_paths = state.vars.get("_skipped_paths") or []
            while (
                items is not None
                and iteration < len(items)
                and f"{step.id}#{iteration + 1}" in skipped_paths
            ):
                iteration += 1
                state.loop_state[qpath] = iteration
            if items is not None and iteration >= len(items):
                break
            if await self._budget_exhausted(state, step, items, iteration):
                budget_blocked = True
                break

            if items is not None:
                state.vars["item"] = items[iteration]
            # contexto de iteración — un loop sin over deja
            # de iterar ciego (el body/until saben qué vuelta corren).
            state.vars["iter"] = iteration + 1
            state.vars["max_iter"] = step.max_iter
            if items is not None:
                cycle_item = state.run_state.find(path=f"{step.id}#{iteration + 1}")
                if cycle_item is not None and cycle_item.status == "pending":
                    cycle_item.status = "running"

            inner_prefix = f"{prefix}{step.id}#{iteration + 1}/"
            # Cota por ítem: el scope de la vuelta se acota a max_tokens_per_item
            # (secuencial). En parallel el lote comparte un techo único: el
            # campo no aplica.
            scope = step.budget_tokens
            if step.max_tokens_per_item and items is not None and not step.parallel:
                scope = min(scope or step.max_tokens_per_item, step.max_tokens_per_item)
            if scope:
                await self._rt.begin_budget_scope(scope)
            try:
                await self._exec_steps(cw, step.body, state, prefix=inner_prefix)
            finally:
                if scope:
                    await self._rt.end_budget_scope()

            cycle_path = f"{step.id}#{iteration + 1}"
            cycle_item = state.run_state.find(path=cycle_path) if items is not None else None
            if (
                items is not None
                and cycle_item is not None
                and cycle_item.status in ("failed", "unmeasured")
            ):
                retries = retries_by_cycle.get(cycle_path, 0)
                if retries < step.retry_on_verification:
                    retries_by_cycle[cycle_path] = retries + 1
                    # el hint se arma ANTES de limpiar la evidencia: resetear
                    # primero convertía el reintento en un "sin medición" ciego.
                    hint = self._retry_hint(cycle_item, self._produces_del_body(step))
                    self._reset_cycle(state, inner_prefix, cycle_item)
                    state.vars["_retry_hint"] = hint
                    continue
                if (
                    step.replan is not None
                    and step.replan.max > 0
                    and len(state.run_state.replans) < step.replan.max
                ):
                    trigger = (
                        "verification_failed" if cycle_item.status == "failed" else "unmeasured"
                    )
                    if trigger in step.replan.triggers:
                        decision, replan_tokens = await self._request_replan(
                            state, step, cycle_item
                        )
                        before = [p.goal for p in state.run_state.plan if p.kind == "subtask"]
                        applied = self._apply_replan(state, step, cycle_item, decision, iteration)
                        after = [p.goal for p in state.run_state.plan if p.kind == "subtask"]
                        state.run_state.replans.append(
                            ReplanRecord(
                                at=datetime.datetime.now(datetime.UTC).isoformat(),
                                trigger=trigger,
                                reason=decision.reason,
                                action=applied,
                                before=before,
                                after=after,
                                tokens=replan_tokens,
                            )
                        )
                        if applied == "retry":
                            if decision.items:
                                items[iteration] = decision.items[0].goal
                                state.vars["item"] = items[iteration]
                            hint = self._retry_hint(cycle_item, self._produces_del_body(step))
                            self._reset_cycle(state, inner_prefix, cycle_item)
                            state.vars["_retry_hint"] = hint
                            continue
                        if applied == "replace":
                            items = items[:iteration] + [item.goal for item in decision.items]
                            # el `over` es la fuente del loop Y del resume: sin
                            # actualizarlo, la re-materialización reagrega los
                            # ítems originales como pendientes fantasma y un
                            # resume vuelve a correr el plan viejo.
                            if step.over:
                                state.vars[step.over] = list(items)
                            self._reset_cycle(state, inner_prefix, cycle_item)
                            continue
                        if applied == "skip":
                            iteration += 1
                            state.loop_state[qpath] = iteration
                            continue
                        for idx in range(iteration, len(items)):
                            pending_item = state.run_state.find(path=f"{step.id}#{idx + 1}")
                            if pending_item is not None:
                                pending_item.status = "blocked"
                                pending_item.last_error = decision.reason or "replan stop"
                        stopped_by_replan = True
                        break
            iteration += 1
            state.loop_state[qpath] = iteration

            if step.until is not None:
                if await self._check_until(step.until, state, qpath):
                    until_hit = True
                    break

        if (
            items is not None
            and iteration < len(items)
            and not until_hit
            and not stopped_by_replan
            and not budget_blocked
        ):
            # el tope truncó la lista: los ítems sin ejecutar no pueden
            # desaparecer en silencio (el run seguiría "completed" habiendo
            # dropeado parte del trabajo).
            dropped = len(items) - iteration
            logger.warning(
                "loop %s truncado por max_iter=%d: %d/%d ítems sin ejecutar",
                qpath,
                step.max_iter,
                dropped,
                len(items),
            )
            self._mark_degraded(f"loop:{qpath}", f"loop:{qpath}")
        if loop_item is not None and loop_item.status != "blocked":
            if stopped_by_replan or budget_blocked:
                loop_item.status = "unmeasured"
            elif step.until is not None:
                loop_item.status = "delivered" if until_hit else "unmeasured"
                if until_hit:
                    loop_item.last_error = None
                    # El until ES la verificación del loop: si terminó verde,
                    # los stalls de sus vueltas quedan recuperados (misma
                    # semántica que el verdict de un tool-step). Sin esto un
                    # run con la suite verde cerraría Parcial por stalls de
                    # ciclos que el until ya validó.
                    recover = getattr(self._rt, "recover_steps", None)
                    if callable(recover):
                        recover(f"{qpath}#")
            else:
                truncated = items is not None and iteration < len(items)
                if truncated:
                    loop_item.status = "unmeasured"
                else:
                    loop_item.status = "delivered"
                    loop_item.last_error = None

    def _reset_cycle(self, state: _State, prefix: str, item: PlanItem) -> None:
        """Deja el ciclo limpio para un retry: completed/results/evidencia."""
        state.completed = {p for p in state.completed if not p.startswith(prefix)}
        state.results[:] = [
            r for r in state.results if not str(r.get("id") or "").startswith(prefix)
        ]
        item.status = "pending"
        item.verification = None
        item.last_error = None

    def _retry_hint(self, item: PlanItem, produces: list[str] | None = None) -> str:
        verification = item.verification
        if verification is not None:
            detail = (
                f"verificación {verification.tool}: passed={verification.passed}; "
                f"{verification.output_tail}"
            )
        elif item.last_error:
            detail = item.last_error
        else:
            detail = "sin medición"
        files = ", ".join(item.files_written[:5]) or "ninguno"
        expected = ", ".join(produces or []) or "el artefacto del contrato"
        return (
            "REINTENTO: el intento anterior no cumplió el contrato. "
            f"Evidencia: {detail}. Archivos: {files}. Artefacto esperado: {expected}. "
            "No repitas la misma acción sin cambiar algo; escribí el artefacto "
            "faltante y verificá con la tool del ciclo."
        )[:1500]

    @staticmethod
    def _produces_del_body(step: LoopStep) -> list[str]:
        return [p for s in step.body if isinstance(s, AgentStep) for p in s.produces]

    async def _request_replan(
        self, state: _State, step: LoopStep, item: PlanItem | None, trigger: str = ""
    ) -> tuple[ReplanDecision, int]:
        policy = step.replan
        if policy is None:
            return ReplanDecision(action="stop", reason="sin política de replan"), 0
        self._sync_budget(state)
        budget = state.run_state.budget
        available = budget.available_for_work() if budget.limit else policy.min_remaining_tokens
        # el replanner no puede comerse la reserva final: sin margen, stop.
        # El trigger `budget` puede usar la reserva de replan (para eso está);
        # los demás exigen el mínimo declarado por la política.
        replan_budget = available + (budget.reserve_replan if trigger == "budget" else 0)
        min_needed = _CYCLE_TOKEN_ESTIMATE if trigger == "budget" else policy.min_remaining_tokens
        if budget.limit and replan_budget < min_needed:
            return (
                ReplanDecision(
                    action="stop",
                    reason=(
                        f"sin budget para replanificar: {available} < "
                        f"{policy.min_remaining_tokens}"
                    ),
                ),
                0,
            )
        try:
            from tools.orchestrator import get_llm_token_usage

            tokens_before = get_llm_token_usage()
        except Exception:
            tokens_before = 0
        decision = await self._rt.replan(
            goal=state.run_state.goal,
            plan=[p.to_dict(public=True) for p in state.run_state.plan if p.kind == "subtask"],
            # el planner decide sobre la EVIDENCIA (tail incluido): public=True
            # la borraba y lo dejaba eligiendo a ciegas.
            failed_item=item.to_dict() if item is not None else None,
            evidence={
                "verification": (
                    (item.verification.to_dict() if item.verification is not None else None)
                    if item is not None
                    else None
                ),
                "files_written": list(item.files_written) if item is not None else [],
                "last_error": item.last_error if item is not None else None,
                "goal": item.goal if item is not None else "",
                "trigger": trigger,
            },
            remaining_tokens=available,
            max_new_items=policy.max_new_items,
            model_role=policy.model_role,
        )
        try:
            tokens = max(0, get_llm_token_usage() - tokens_before)
        except Exception:
            tokens = 0
        state.run_state.budget.spent += tokens
        return decision, tokens

    def _apply_replan(
        self,
        state: _State,
        step: LoopStep,
        item: PlanItem | None,
        decision: ReplanDecision,
        iteration: int,
    ) -> str:
        policy = step.replan
        if policy is None:
            return "stop"
        budget = state.run_state.budget
        action = decision.action
        if action == "replace":
            if not decision.reason.strip():
                return "stop"
            if not decision.items or len(decision.items) > policy.max_new_items:
                return "stop"
            goals = [new.goal for new in decision.items]
            if len(set(goals)) != len(goals):
                return "stop"
            if iteration + len(decision.items) > step.max_iter:
                return "stop"
            if budget.limit and budget.available_for_work() < len(
                decision.items
            ) * self._cycle_estimate(state, step):
                return "stop"
            for plan_item in list(state.run_state.plan):
                if plan_item.kind != "subtask" or not plan_item.path:
                    continue
                if not plan_item.path.startswith(f"{step.id}#"):
                    continue
                try:
                    idx = int(plan_item.path.split("#", 1)[1])
                except ValueError:
                    continue
                if idx >= iteration + 1:
                    state.run_state.plan.remove(plan_item)
            for idx, new_item in enumerate(decision.items, iteration + 1):
                path = f"{step.id}#{idx}"
                state.run_state.plan.append(
                    PlanItem(kind="subtask", id=path, goal=new_item.goal, path=path)
                )
            return "replace"
        if action == "skip":
            if not decision.skipped:
                return "stop"
            # `satisfied_by` debe referenciar evidencia MEDIDA del run: un id
            # con verificación verde o un archivo que el run escribió. Una
            # justificación inventada no salta trabajo.
            measured_files = {f for p in state.run_state.plan for f in p.files_written}
            measured_ids = {
                p.id
                for p in state.run_state.plan
                if p.verification is not None and p.verification.passed is True
            }
            targets: list[tuple[PlanItem, str]] = []
            for skip in decision.skipped:
                target = state.run_state.find(item_id=skip.item_id) or state.run_state.find(
                    path=skip.item_id
                )
                if target is None:
                    return "stop"
                evidence = (skip.satisfied_by or "").strip()
                if not evidence:
                    return "stop"
                if evidence not in measured_ids and not any(
                    evidence == f or f.endswith("/" + evidence) for f in measured_files
                ):
                    return "stop"
                targets.append((target, evidence))
            if item is not None and not any(target is item for target, _ in targets):
                return "stop"
            skipped_paths = state.vars.setdefault("_skipped_paths", [])
            for target, evidence in targets:
                if target.status in ("delivered", "failed") and target is not item:
                    continue  # no se reescribe evidencia ya medida de OTRO item
                target.status = "skipped"
                target.satisfied_by = evidence
                target.last_error = None
                if target.path and target.path not in skipped_paths:
                    skipped_paths.append(target.path)
            return "skip"
        if action == "retry":
            return "retry"
        return "stop"

    # ── Loop paralelo ─────────────────────────────────────────────

    async def _loop_parallel(
        self,
        cw: CompiledWorkflow,
        step: LoopStep,
        state: _State,
        *,
        qpath: str,
        prefix: str,
    ) -> None:
        raw = state.vars.get(step.over or "")
        if raw is None:
            raise EngineError(f"{qpath}: loop.over '${step.over}' sin valor")
        items: list[Any] = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        total = min(len(items), step.max_iter)
        done = state.loop_state.get(qpath, 0)
        semaphore = asyncio.Semaphore(step.parallel_max)

        # vars de FLUJO por ítem: no se mergean al estado padre (el valor de un
        # hermano cualquiera clobberaría al resto; `iter` es el índice del ítem).
        _FLOW_VARS = ("item", "last_output", "last_gate", "iter", "max_iter")

        async def run_item(idx: int) -> None:
            # Vars POR ÍTEM: overlay aislado; completed/results/errors son
            # compartidos (resume granular por camino cualificado ciclo#N/id).
            overlay = dict(state.vars)
            overlay["item"] = items[idx]
            # `$iter`/`$max_iter` también en paralelo (el índice del ítem):
            # sin ellos un preset no puede declarar una ruta por ciclo que
            # funcione en ambos modos — el body paralelo moría en interpolación.
            overlay["iter"] = idx + 1
            overlay["max_iter"] = step.max_iter
            before = dict(overlay)
            item_state = _State(
                vars_=overlay,
                resume=None,
                max_interp_chars=state.max_interp_chars,
                cycle_token_estimate=state.cycle_token_estimate,
            )
            item_state.completed = state.completed
            item_state.loop_state = state.loop_state
            item_state.children = state.children
            item_state.results = state.results
            item_state.errors = state.errors
            item_state.run_state = state.run_state
            inner_prefix = f"{prefix}{step.id}#{idx + 1}/"
            cycle_item = state.run_state.find(path=f"{step.id}#{idx + 1}")
            if cycle_item is not None and cycle_item.status == "pending":
                cycle_item.status = "running"
            async with semaphore:
                await self._exec_steps(cw, step.body, item_state, prefix=inner_prefix)
            # Merge SOLO de claves que ESTE ítem escribió (comparación contra
            # su propio snapshot) — evita lost-update entre ítems concurrentes.
            for k, v in overlay.items():
                if k in _FLOW_VARS:
                    continue
                if before.get(k) != v:
                    state.vars[k] = v
            state.loop_state[qpath] = state.loop_state.get(qpath, 0) + 1

        # rutas de test ya consumidas: lista COMPARTIDA por referencia —
        # los overlays la copian como objeto y pick_line muta in place, así
        # la inferencia de un ítem es visible para los demás sin carreras
        state.vars.setdefault("_test_paths_usados", [])

        loop_item = state.run_state.find(item_id=step.id)
        if loop_item is not None:
            loop_item.status = "running"
        if (
            state.run_state.budget.limit
            and state.run_state.budget.available_for_work() < state.cycle_token_estimate
        ):
            for idx in range(done, total):
                item = state.run_state.find(path=f"{step.id}#{idx + 1}")
                if item is not None:
                    item.status = "blocked"
                    item.last_error = "budget"
            if loop_item is not None:
                loop_item.status = "blocked"
            self._rt.emit(
                kind="budget_exhausted",
                path=step.id,
                status="blocked",
                run_state=state.run_state.public(),
            )
            return
        budget_capped = False
        if state.run_state.budget.limit:
            affordable = max(
                1,
                state.run_state.budget.available_for_work() // state.cycle_token_estimate,
            )
            if total - done > affordable:
                budget_capped = True
                for idx in range(done + affordable, total):
                    item = state.run_state.find(path=f"{step.id}#{idx + 1}")
                    if item is not None:
                        item.status = "blocked"
                        item.last_error = "budget"
                total = done + affordable
                self._rt.emit(
                    kind="budget_exhausted",
                    path=step.id,
                    status="blocked",
                    run_state=state.run_state.public(),
                )
        tasks = [asyncio.create_task(run_item(i)) for i in range(done, total)]
        # LOTE: los hermanos comparten el techo (estado de presupuesto único)
        if step.budget_tokens:
            await self._rt.begin_budget_scope(step.budget_tokens)
        try:
            await asyncio.gather(*tasks)
        except (_EnginePaused, _EngineFailed, _EngineStopped):
            # pausa/fallo/stop de UN ítem: cancelar el resto (nada corre en
            # fondo mientras el snapshot se guarda) y NO contar ítems incompletos.
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            if step.budget_tokens:
                await self._rt.end_budget_scope()

        executed = state.loop_state.get(qpath, 0)
        if executed < len(items) and not budget_capped:
            logger.warning(
                "loop paralelo %s truncado por max_iter=%d: %d/%d ítems sin ejecutar",
                qpath,
                step.max_iter,
                len(items) - executed,
                len(items),
            )
            self._mark_degraded(f"loop:{qpath}", f"loop:{qpath}")
        if loop_item is not None:
            if executed >= len(items):
                loop_item.status = "delivered"
                loop_item.last_error = None
            else:
                loop_item.status = "unmeasured"

    def _registrar_sin_medicion(self, state: _State, qpath: str, detalle: str) -> None:
        """Sin medición: DOS vueltas de gracia (el body puede reparar el
        artefacto que falta) y a la tercera consecutiva, fallo accionable.

        Tolerar indefinidamente quema el presupuesto del loop sin poder
        evaluar nunca su meta; morir en la primera medición no le daba
        oportunidad. Cualquier medición válida resetea la racha. La segunda
        gracia existe porque un modelo thrash-eando puede recuperar en la
        vuelta siguiente (medido: un run murió al segundo no-medido y el
        relanzamiento inmediato completó) — una vuelta más acota el loop
        ciego sin condenar el run por un bache pasajero del modelo.
        """
        fallos = state.until_unmeasured.get(qpath, 0) + 1
        state.until_unmeasured[qpath] = fallos
        if fallos > 2:
            raise EngineError(
                f"{qpath}: la verificación del until sigue sin poder medirse "
                f"tras {fallos} intentos ({detalle}) — ¿el artefacto del paso "
                "anterior no se escribió?"
            )
        logger.warning("until %s: sin medición (%s) — una vuelta más del loop", qpath, detalle)

    def _record_until_verification(
        self,
        state: _State,
        qpath: str,
        *,
        tool: str,
        args: dict[str, Any],
        passed: bool | None,
        counts: dict[str, Any],
        tail: str,
    ) -> None:
        """El `until` de un loop sin `over` ES la verificación de la feature."""
        item = state.run_state.find_for_path(qpath)
        if item is not None and item.kind == "subtask":
            item.verification = Verification(
                tool=tool,
                args=dict(args),
                passed=passed,
                counts=dict(counts),
                output_tail=str(tail or "")[:500],
            )

    async def _check_until(self, until: Any, state: _State, qpath: str) -> bool:
        # args declarados en el until, interpolados con las vars del
        # estado — sin esto la tool se llamaría SIEMPRE con {} y toda
        # tool con parámetros requeridos moriría en TypeError (el until
        # jamás se cumpliría → loops siempre agotan max_iter).
        args = {
            k: (
                _interpolate(v, state.vars, qpath, state.max_interp_chars)
                if isinstance(v, str)
                else v
            )
            for k, v in getattr(until, "args", {}).items()
        }
        if until.type == "tool":
            result = await self._rt.call_tool(until.tool, args)
            counts = (
                {
                    key: result.get(key)
                    for key in ("passed_count", "failed_count", "error_count", "total_count")
                    if isinstance(result, dict) and key in result
                }
                if isinstance(result, dict)
                else {}
            )
            tail = str(result.get("output") or "") if isinstance(result, dict) else ""
            # Clave AUSENTE = la verificación no corrió (ruta inexistente,
            # entorno roto): sin medición, no "medido en rojo". Confundirlo
            # agotaba max_iter × presupuesto sobre un artefacto inexistente.
            if not isinstance(result, dict) or until.check not in result:
                self._registrar_sin_medicion(state, qpath, f"clave '{until.check}' ausente")
                self._record_until_verification(
                    state,
                    qpath,
                    tool=until.tool,
                    args=args,
                    passed=None,
                    counts=counts,
                    tail=tail,
                )
                return False
            state.until_unmeasured.pop(qpath, None)
            passed = bool(result[until.check])
            self._record_until_verification(
                state,
                qpath,
                tool=until.tool,
                args=args,
                passed=passed,
                counts=counts,
                tail=tail,
            )
            return passed
        if until.type == "metric":
            result = await self._rt.call_tool(until.tool, args)
            raw = result.get(until.metric) if isinstance(result, dict) else None
            if not isinstance(raw, (int, float)):
                self._registrar_sin_medicion(state, qpath, f"'{until.metric}' no numérico: {raw!r}")
                self._record_until_verification(
                    state,
                    qpath,
                    tool=until.tool,
                    args=args,
                    passed=None,
                    counts={until.metric: raw},
                    tail=str(result.get("output") or "") if isinstance(result, dict) else "",
                )
                return False
            state.until_unmeasured.pop(qpath, None)
            passed = _metric_ok(float(raw), until.op, float(until.value))
            self._record_until_verification(
                state,
                qpath,
                tool=until.tool,
                args=args,
                passed=passed,
                counts={until.metric: raw},
                tail=str(result.get("output") or "") if isinstance(result, dict) else "",
            )
            return passed
        # agent: no-determinismo acotado — expect validado + fallback
        context = str(state.vars.get("last_output") or "")[: state.max_interp_chars]
        answer = await self._rt.evaluate(until.question, until.expect, context)
        normalized = str(answer).strip().upper()
        expect_norm = str(until.expect).strip().upper()
        matched = normalized == expect_norm
        # Matching tolerante: el modelo responde en prosa ("No: el test
        # está truncado") — el primer token basta cuando coincide con el
        # enum esperado; una respuesta genuinamente distinta cae al fallback.
        if not matched:
            first_token = re.split(r"[\s:;,.\-—¡!¿?]+", normalized)[0] if normalized else ""
            matched = first_token == expect_norm
        self._record_until_verification(
            state,
            qpath,
            tool="evaluate",
            args={},
            passed=matched,
            counts={},
            tail=str(answer),
        )
        if matched:
            return True
        if until.fallback == "continue":
            # rechazo legítimo del evaluador: OTRA vuelta del loop (el
            # feedback queda en last_output para el siguiente intento),
            # acotada por max_iter — no es error ni salida
            return False
        if until.fallback == "exit":
            logger.warning(
                "until-agent %s: respuesta inválida %r → fallback exit",
                qpath,
                answer,
            )
            return True
        raise _EngineFailed(
            f"{qpath}: until-agent respuesta inválida {answer!r} "
            f"(esperaba '{until.expect}', fallback=fail)"
        )

    # ── Parallel ───────────────────────────────────────────────────────

    async def _parallel(
        self,
        cw: CompiledWorkflow,
        step: ParallelStep,
        state: _State,
        *,
        prefix: str,
    ) -> None:
        roots: dict[str, list[StepSpec]] = {}
        deps_of: dict[str, list[str]] = {}
        for branch in step.branches:
            root_id = branch.steps[0].id
            roots[root_id] = branch.steps
            deps_of[root_id] = list(branch.depends_on)

        for deps in deps_of.values():
            for dep in deps:
                if dep not in roots:
                    raise _EngineFailed(
                        f"{step.id}: depends_on '{dep}' no es raíz de una rama del parallel"
                    )

        semaphore = asyncio.Semaphore(step.max_parallel)
        executed: set[str] = set()

        async def run_branch(root_id: str) -> None:
            async with semaphore:
                await self._exec_steps(
                    cw, roots[root_id], state, prefix=f"{prefix}{step.id}/{root_id}/"
                )
            executed.add(root_id)

        pending = dict.fromkeys(roots)
        while pending:
            ready = [rid for rid in pending if all(d in executed for d in deps_of[rid])]
            if not ready:
                raise _EngineFailed(
                    f"{step.id}: dependencias circulares o insatisfacibles en parallel"
                )
            await asyncio.gather(*(run_branch(rid) for rid in ready))
            for rid in ready:
                pending.pop(rid)
