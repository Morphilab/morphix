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
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from orchestration.dsl.compiler import CompiledWorkflow
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

_VAR_REF_RE = re.compile(r"\$([a-z_][a-z0-9_]*)")

__all__ = ["WorkflowEngine", "EngineRuntime", "EngineResult", "EngineError"]


class EngineError(Exception):
    """Violación de invariante interna del motor (fail-loud)."""


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
    ) -> str: ...

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

    def emit(self, **payload: Any) -> None: ...


@dataclass
class EngineResult:
    status: str  # "completed" | "failed" | "paused"
    outputs: dict[str, Any] = field(default_factory=dict)
    final_output: str | None = None
    vars: dict[str, Any] = field(default_factory=dict)
    failure: str | None = None
    errors: list[str] = field(default_factory=list)
    snapshot: dict[str, Any] | None = None
    paused_question: str | None = None
    paused_options: list[str] = field(default_factory=list)
    paused_step_id: str | None = None
    paused_kind: str = "checkpoint"


_MAX_INTERP_CHARS = 2000


def _interpolate(text: str, vars_: dict[str, Any], where: str) -> str:
    """Sustituye $var por su valor. Var indefinida → EngineError (fail-loud).

    El valor sustituido se acota (paridad con decide/evaluate): sin esto, un
    $last_output grande re-inyectado en el goal de cada iteración de un loop
    crecía el prompt de forma cuadrática y quemaba el presupuesto.
    """

    def _sub(match: re.Match[str]) -> str:
        var = match.group(1)
        if var not in vars_:
            raise EngineError(f"{where}: variable '${var}' sin valor en runtime")
        return str(vars_[var])[:_MAX_INTERP_CHARS]

    return _VAR_REF_RE.sub(_sub, text)


def _eval_when(when: str | None, vars_: dict[str, Any], query: str) -> bool:
    if when is None:
        return True
    if when == "if_blockers":
        return bool(BLOCKER_RE.search(str(vars_.get("last_gate") or "")))
    if when.startswith("query_contains:"):
        token = when.split(":", 1)[1].lower()
        return token in str(query).lower()
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

    def __init__(self, vars_: dict[str, Any], resume: dict[str, Any] | None):
        self.vars = vars_
        self.completed: set[str] = set((resume or {}).get("completed") or [])
        self.loop_state: dict[str, int] = dict((resume or {}).get("loop_state") or {})
        self.children: dict[str, dict[str, Any]] = dict((resume or {}).get("children") or {})
        self.results: list[dict[str, Any]] = list(
            (resume or {}).get("results") or []
        )  # {id, output} en orden de ejecución
        self.errors: list[str] = []


class WorkflowEngine:
    """Ejecuta un CompiledWorkflow contra un EngineRuntime."""

    def __init__(self, runtime: EngineRuntime):
        self._rt = runtime

    async def run(
        self,
        cw: CompiledWorkflow,
        *,
        inputs: dict[str, Any] | None = None,
        resume: dict[str, Any] | None = None,
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

        state = _State(vars_, resume)
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
        return self._result(cw, state, status="completed")

    # ── Resultado y snapshot ───────────────────────────────────────────

    def _result(
        self,
        cw: CompiledWorkflow,
        state: _State,
        *,
        status: str,
        failure: str | None = None,
    ) -> EngineResult:
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
            }
        return EngineResult(
            status=status,
            outputs=outputs,
            final_output=final,
            vars=state.vars,
            failure=failure,
            errors=state.errors,
            snapshot=snapshot,
        )

    # ── Secuencia ──────────────────────────────────────────────────────

    async def _exec_steps(
        self,
        cw: CompiledWorkflow,
        steps: list[StepSpec],
        state: _State,
        *,
        prefix: str,
    ) -> None:
        for step in steps:
            qpath = f"{prefix}{step.id}"
            if qpath in state.completed:
                continue
            if not _eval_when(step.when, state.vars, str(state.vars.get("query") or "")):
                continue
            self._rt.emit(kind="step", path=qpath, status="running")
            try:
                await self._exec_step(cw, step, state, qpath=qpath, prefix=prefix)
            except _EnginePaused as p:
                if not p.step_id:
                    p.step_id = step.id
                raise
            except _EngineFailed:
                raise  # señal de control de flujo, NO error de step
            except PauseRequested as pr:
                # un adapter pide pausa humana (clarificación/aprobación);
                # lleva el step: el resume inyecta la respuesta ahí
                raise _EnginePaused(pr.question, pr.options, kind=pr.kind, step_id=step.id) from pr
            except Exception as e:
                if step.on_error == "continue":
                    state.errors.append(f"{qpath}: {e}")
                    logger.warning("step %s falló (on_error=continue): %s", qpath, e)
                    self._rt.emit(kind="step", path=qpath, status="error")
                    continue
                raise _EngineFailed(f"{qpath}: {e}") from e
            state.completed.add(qpath)
            self._rt.emit(kind="step", path=qpath, status="completed")
            # commit_after: decisión declarada del workflow —
            # el MOTOR committea tras completar steps con id declarado.
            # Resume no re-committea: el completed-check de arriba salta
            # steps ya terminados antes de llegar aquí.
            if step.id in cw.dsl.commit_after:
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
        from orchestration.dsl.registry import get_primitive

        executor = get_primitive(step.plugin)
        if executor is None:
            raise EngineError(
                f"{qpath}: primitiva plugin '{step.plugin}' no registrada "
                f"(disponibles: see registry.known_primitives)"
            )
        await executor(step, state, self._rt, qpath)

    # ── Steps concretos ────────────────────────────────────────────────

    async def _agent(self, step: AgentStep, state: _State, qpath: str) -> None:
        prompt = _interpolate(step.goal or "", state.vars, qpath)
        attempts = step.retry_max + 1
        output: str | None = None
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                output = await self._rt.call_agent(
                    step.agent,
                    prompt,
                    model_role=step.model_role,
                    step_id=step.id,
                    path=qpath,
                )
                last_error = None
                break
            except (_EnginePaused, PauseRequested):
                raise  # la pausa humana jamás es objeto de retry
            except Exception as e:
                last_error = e
                if attempt < attempts:
                    logger.warning(
                        "agent %s intento %d/%d falló (%s) — reintentando",
                        qpath,
                        attempt,
                        attempts,
                        e,
                    )
        if last_error is not None:
            raise last_error
        assert output is not None
        state.vars["last_output"] = output
        for var in step.outputs:
            state.vars[var] = output
        state.results.append({"id": step.id, "output": output, "status": "completed"})
        if step.gate:
            # Semántica pipeline: el stage-gate publica su salida completa;
            # `when: if_blockers` la evalúa con BLOCKER_RE.
            state.vars["last_gate"] = output
            if isinstance(step.gate, str) and re.search(step.gate, output):
                state.vars[f"gate_{step.id}"] = True

    async def _tool(self, step: ToolStep, state: _State, qpath: str) -> None:
        args = {
            k: _interpolate(v, state.vars, qpath) if isinstance(v, str) else v
            for k, v in step.args.items()
        }
        result = await self._rt.call_tool(step.tool, args)
        state.vars["last_output"] = result
        for var, key in step.outputs.items():
            state.vars[var] = result.get(key) if isinstance(result, dict) else result

    async def _decompose(self, step: DecomposeStep, state: _State, qpath: str) -> None:
        result = await self._rt.decompose(str(state.vars.get("query") or ""), step.strategy)
        state.vars[step.output] = result
        state.vars["last_output"] = result

    async def _checkpoint(self, step: CheckpointStep, state: _State, qpath: str) -> None:
        question = _interpolate(step.question, state.vars, qpath)
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
        question = _interpolate(step.question, state.vars, qpath)
        context = str(state.vars.get("last_output") or "")[:2000]
        answer = await self._rt.decide(question, step.options, context, step_id=step.id)
        if answer not in step.options:
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
                _interpolate(source, state.vars, qpath)
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
        while True:
            if items is not None and iteration >= len(items):
                break
            if items is None and iteration >= step.max_iter:
                break
            if items is not None and iteration >= step.max_iter:
                break  # tope duro incluso iterando lista

            if items is not None:
                state.vars["item"] = items[iteration]
            # contexto de iteración — un loop sin over deja
            # de iterar ciego (el body/until saben qué vuelta corren).
            state.vars["iter"] = iteration + 1
            state.vars["max_iter"] = step.max_iter

            inner_prefix = f"{prefix}{step.id}#{iteration + 1}/"
            await self._exec_steps(cw, step.body, state, prefix=inner_prefix)
            iteration += 1
            state.loop_state[qpath] = iteration

            if step.until is not None:
                if await self._check_until(step.until, state, qpath):
                    break

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

        _FLOW_VARS = ("item", "last_output", "last_gate")

        async def run_item(idx: int) -> None:
            # Vars POR ÍTEM: overlay aislado; completed/results/errors son
            # compartidos (resume granular por camino cualificado ciclo#N/id).
            overlay = dict(state.vars)
            overlay["item"] = items[idx]
            before = dict(overlay)
            item_state = _State(vars_=overlay, resume=None)
            item_state.completed = state.completed
            item_state.loop_state = state.loop_state
            item_state.children = state.children
            item_state.results = state.results
            item_state.errors = state.errors
            inner_prefix = f"{prefix}{step.id}#{idx + 1}/"
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

        tasks = [asyncio.create_task(run_item(i)) for i in range(done, total)]
        try:
            await asyncio.gather(*tasks)
        except (_EnginePaused, _EngineFailed):
            # pausa/fallo de UN ítem: cancelar el resto (nada corre en fondo
            # mientras el snapshot se guarda) y NO contar ítems incompletos.
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _check_until(self, until: Any, state: _State, qpath: str) -> bool:
        # args declarados en el until, interpolados con las vars del
        # estado — sin esto la tool se llamaría SIEMPRE con {} y toda
        # tool con parámetros requeridos moriría en TypeError (el until
        # jamás se cumpliría → loops siempre agotan max_iter).
        args = {
            k: _interpolate(v, state.vars, qpath) if isinstance(v, str) else v
            for k, v in getattr(until, "args", {}).items()
        }
        if until.type == "tool":
            result = await self._rt.call_tool(until.tool, args)
            value = result.get(until.check) if isinstance(result, dict) else None
            return bool(value)
        if until.type == "metric":
            result = await self._rt.call_tool(until.tool, args)
            raw = result.get(until.metric) if isinstance(result, dict) else None
            if not isinstance(raw, (int, float)):
                raise EngineError(
                    f"{qpath}: until.metric '{until.metric}' no es numérico ({raw!r})"
                )
            return _metric_ok(float(raw), until.op, float(until.value))
        # agent: no-determinismo acotado — expect validado + fallback
        context = str(state.vars.get("last_output") or "")[:2000]
        answer = await self._rt.evaluate(until.question, until.expect, context)
        normalized = str(answer).strip().upper()
        if normalized == str(until.expect).strip().upper():
            return True
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
