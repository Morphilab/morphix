# orchestration/dsl/conformance.py
"""Conformance ejecutable de presets DSL.

`validate` comprueba ESTRUCTURA y semántica estática; esto comprueba que el
programa CORRE: cada preset se ejecuta contra el motor real + el adapter real
+ el dispatch real de tools con handlers que CLONAN la firma real, y solo lo
difuso se stubbea (LLM, decompose, decisiones, agregador).

Un preset puede compilar+validar y morir en runtime (un until sin args →
TypeError → loops que siempre agotan max_iter). Toda feature nueva del DSL
debe sumar un caso aquí (`tests/test_dsl_conformance.py`).

Uso:
- tests: `tests/test_dsl_conformance.py` ejecuta TODOS los presets.
- CLI: `python -m orchestration.dsl.cli validate <preset> --conformance`.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

from orchestration.dsl.compiler import FileSystemCatalog, compile_workflow
from orchestration.dsl.engine import WorkflowEngine
from orchestration.dsl.runtime_adapter import ProductionRuntime
from orchestration.dsl.validator import validate_workflow

__all__ = ["ConformanceReport", "simulate_preset", "structural_feedback_errors"]


@dataclass
class ConformanceReport:
    """Resultado de la simulación ejecutable de un preset."""

    name: str
    ok: bool = False
    status: str = ""
    failure: str | None = None
    errors: list[str] = field(default_factory=list)
    semantic_errors: list[str] = field(default_factory=list)
    agent_tasks: list[str] = field(default_factory=list)
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    test_runner_sin_file_path: bool = False

    def summary(self) -> str:
        head = f"✔ '{self.name}' corre a completed" if self.ok else f"❌ '{self.name}' FALLA"
        parts = [head, f"  status={self.status} tareas_agente={len(self.agent_tasks)}"]
        if self.semantic_errors:
            parts.append("  semántica: " + "; ".join(self.semantic_errors))
        if self.failure:
            parts.append(f"  failure: {self.failure}")
        for e in self.errors:
            parts.append(f"  error: {e}")
        if self.test_runner_sin_file_path:
            parts.append("  RC1: test_runner llamado sin file_path (until/tool-step roto)")
        return "\n".join(parts)


def structural_feedback_errors(dsl: Any) -> list[str]:
    """Guard estático: todo loop SIN over (iteraciones ciegas) debe
    alimentar a su body con contexto que cambie por iteración — $last_output,
    $iter, o una var output de un step del propio body (patrón reflexion
    $critica). Sin esto el agente repite trabajo desde cero en cada vuelta."""

    def _iter_tree(steps: list[Any]):
        for step in steps:
            yield step
            if hasattr(step, "body"):
                yield from _iter_tree(step.body)
            branches = getattr(step, "branches", None)
            if branches:
                for branch_steps in branches.values() if isinstance(branches, dict) else branches:
                    yield from _iter_tree(branch_steps)

    errores: list[str] = []
    for loop in _iter_tree(dsl.steps):
        if not hasattr(loop, "body") or getattr(loop, "over", None):
            continue  # loop con over itera ítems distintos: feedback inherente
        body_outputs: set[str] = set()
        for inner in _iter_tree(loop.body):
            body_outputs.update(getattr(inner, "outputs", None) or [])
        feedback = {"$last_output", "$iter"} | {f"${v}" for v in body_outputs}
        for inner in _iter_tree(loop.body):
            goal = getattr(inner, "goal", None)
            if goal and not any(v in goal for v in feedback):
                errores.append(
                    f"{loop.id}/{inner.id}: goal de loop sin over NO consume "
                    f"feedback de iteración ({sorted(feedback)}) — el agente "
                    "itera CIEGO (RC2)"
                )
    return errores


class _Patches:
    """Set/restore manual de atributos e ítems (producto no depende de pytest)."""

    def __init__(self) -> None:
        self._saved: list[tuple[Any, str, Any]] = []
        self._saved_items: list[tuple[dict, Any, Any]] = []

    def set(self, obj: Any, name: str, value: Any) -> None:
        self._saved.append((obj, name, obj.__dict__.get(name, _MISSING)))
        setattr(obj, name, value)

    def setitem(self, mapping: dict, key: Any, value: Any) -> None:
        self._saved_items.append((mapping, key, mapping.get(key, _MISSING)))
        mapping[key] = value

    def restore(self) -> None:
        for mapping, key, orig in reversed(self._saved_items):
            if orig is _MISSING:
                mapping.pop(key, None)
            else:
                mapping[key] = orig
        for obj, name, orig in reversed(self._saved):
            if orig is _MISSING:
                if name in obj.__dict__:
                    delattr(obj, name)
            else:
                setattr(obj, name, orig)
        self._saved.clear()
        self._saved_items.clear()


_MISSING = object()


def _clone_signature(handler: Any) -> Any:
    try:
        return inspect.signature(handler)
    except (TypeError, ValueError):
        return None


async def simulate_preset(
    raw: dict,
    *,
    workspace: str = "main",
    project_root: str = "/tmp/proyecto-fake-conformance",
) -> ConformanceReport:
    """Compila, valida y EJECUTA un preset contra la pila real (lo difuso fake).

    Nunca lanza por fallos del preset: todo queda en el report (fail-loud solo
    para bugs de integración del propio simulador).
    """
    import tools.orchestrator as tools_orch
    from orchestration.aggregator import ResultAggregator
    from tools.loader import load_global_tools
    from tools.registry import tools_registry

    name = str(raw.get("name") or "preset")
    report = ConformanceReport(name=name)

    # El registry global solo se llena al arranque de la app — el CLI/tests
    # lo cargan explícitamente (la carga es idempotente).
    load_global_tools()

    try:
        cw = compile_workflow(raw, catalog=FileSystemCatalog(workspace))
    except Exception as e:
        report.semantic_errors = [f"compilación: {e}"]
        return report
    report.semantic_errors = validate_workflow(cw)
    if report.semantic_errors:
        return report

    # ── Tools: handlers fake con firma REAL sobre el registry global ──
    tools_necesitadas: set[str] = set(cw.dsl.tools.allowed or [])

    def _iter_tree(steps: list[Any]):
        for step in steps:
            yield step
            if hasattr(step, "body"):
                yield from _iter_tree(step.body)
            branches = getattr(step, "branches", None)
            if branches:
                for branch_steps in branches.values() if isinstance(branches, dict) else branches:
                    yield from _iter_tree(branch_steps)

    for step in _iter_tree(cw.dsl.steps):
        until = getattr(step, "until", None)
        if until is not None and getattr(until, "tool", None):
            tools_necesitadas.add(until.tool)

    patches = _Patches()
    try:
        for tname in sorted(tools_necesitadas):
            handler = tools_registry.get_tool(tname)
            if handler is None:
                report.semantic_errors.append(
                    f"tool '{tname}' declarada y NO registrada (contrato roto)"
                )
                return report
            sig = _clone_signature(handler)

            async def fake_tool(*args: Any, _name: str = tname, **kwargs: Any) -> dict:
                report.tool_calls.append((_name, dict(kwargs)))
                if _name == "test_runner":
                    # conteos parseables: tests_all_pass=True / passed_count=7
                    return {
                        "success": True,
                        "output": "7 passed, 0 failed in 0.1s",
                        "returncode": 0,
                    }
                return {"success": True, "output": "ok(fake)"}

            if sig is not None:
                fake_tool.__signature__ = sig  # type: ignore[attr-defined] # noqa: B010
            fake_tool.__name__ = f"fake_{tname}"
            patches.setitem(tools_orch.tools_registry._tools, tname, fake_tool)

        # ── Lo difuso: stubs ──
        import orchestration.decomposer as decomposer_mod
        import orchestration.loop as loop_mod

        async def fake_agent_loop(**kwargs: Any) -> dict:
            report.agent_tasks.append(str(kwargs.get("task") or ""))
            return {
                "status": "completed",
                "result": "salida(fake)",
                "files_written": ["src/main.py"],
                "iterations": 1,
                "actions_taken": 1,
            }

        async def fake_decompose(**kwargs: Any) -> list[str]:
            return ["tarea_a", "tarea_b"]

        async def fake_decide(
            self: ProductionRuntime,
            question: str,
            options: list[str],
            context: str,
            *,
            step_id: str = "",
        ) -> str:
            return options[0]

        async def fake_evaluate(
            self: ProductionRuntime, question: str, expect: str, context: str
        ) -> str:
            return expect

        async def fake_checkpoint(
            self: ProductionRuntime, question: str, step_id: str = ""
        ) -> bool:
            return True

        async def fake_commit_after(self: ProductionRuntime, step_id: str) -> None:
            return None

        async def fake_aggregate_results(self: ResultAggregator, **kwargs: Any) -> str:
            return "AGREGADO(fake)"

        patches.set(loop_mod, "execute_agent_loop", fake_agent_loop)
        patches.set(decomposer_mod, "decompose_task", fake_decompose)
        patches.set(ProductionRuntime, "decide", fake_decide)
        patches.set(ProductionRuntime, "evaluate", fake_evaluate)
        patches.set(ProductionRuntime, "checkpoint", fake_checkpoint)
        patches.set(ProductionRuntime, "commit_after_step", fake_commit_after)
        patches.set(ResultAggregator, "aggregate_results", fake_aggregate_results)

        runtime = ProductionRuntime(
            workspace=workspace,
            project_root=project_root,
            tools_allowed=list(cw.dsl.tools.allowed or []),
            query="consulta de conformance",
            emitter=None,
        )

        async def _recording_call_tool(tool: str, args: dict[str, Any]) -> dict:
            report.tool_calls.append((tool, dict(args)))
            return await ProductionRuntime.call_tool(runtime, tool, args)

        patches.set(runtime, "call_tool", _recording_call_tool)

        engine = WorkflowEngine(runtime)  # type: ignore[arg-type]
        result = await engine.run(cw, inputs={"query": "consulta de conformance"})
        report.status = result.status
        report.failure = result.failure
        report.errors = list(result.errors)
        report.ok = result.status == "completed" and not result.errors
    finally:
        patches.restore()

    report.test_runner_sin_file_path = any(
        t == "test_runner" and not a.get("file_path") for t, a in report.tool_calls
    )
    if report.test_runner_sin_file_path:
        report.ok = False
    return report
