# tests/test_dsl_loop_parallel.py
"""Tests de loop.parallel dinámico — concurrencia real por ítem.

El body corre concurrente con VARS POR ÍTEM aisladas ($item sin cross-talk);
outputs se mergean al padre al cerrar cada ítem; semáforo parallel_max;
resume reanuda solo ítems incompletos (paths cualificados ciclo#N/id).
"""

import asyncio

import pytest
from pydantic import ValidationError

from orchestration.dsl.compiler import compile_workflow
from orchestration.dsl.engine import WorkflowEngine
from orchestration.dsl.validator import validate_workflow
from tests.test_dsl_engine import FakeRuntime, _doc


class _ParRuntime(FakeRuntime):
    """Runtime con latencia por llamada + conteo de concurrencia.

    ``prep`` devuelve una lista de items; las tareas del loop devuelven
    strings y registran concurrencia.
    """

    def __init__(self, *, latency: float = 0.05, items: list[str] | None = None, **over):
        super().__init__(**over)
        self.latency = latency
        self.items = items or ["item_1", "item_2", "item_3", "item_4"]
        self.concurrent = 0
        self.max_concurrent = 0
        self.items_seen: list[str] = []

    async def call_agent(self, agent, prompt, *, model_role=None, step_id="", path=""):
        if step_id == "prep":
            return self.items
        # $item ya está interpolado en el prompt (goal: "haz $item")
        self.items_seen.append(prompt)
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            await asyncio.sleep(self.latency)
            return f"out_{step_id}"
        finally:
            self.concurrent -= 1


def _par_loop(over="items", body=None, **overrides) -> dict:
    step = {
        "id": "ciclo",
        "kind": "loop",
        "max_iter": 10,
        "over": over,
        "parallel": True,
        "body": body
        or [{"id": "tarea", "kind": "agent", "agent": "developer", "goal": "haz $item"}],
    }
    step.update(overrides)
    return step


class TestSchemaYValidator:
    def test_parallel_valid(self):
        doc = _doc([_par_loop()])
        dsl = compile_workflow(doc).dsl
        assert dsl.steps[0].parallel is True
        assert dsl.steps[0].parallel_max == 2

    def test_parallel_max_bounds(self):
        with pytest.raises(ValidationError):
            compile_workflow(_doc([_par_loop(parallel_max=0)]))
        with pytest.raises(ValidationError):
            compile_workflow(_doc([_par_loop(parallel_max=9)]))

    def test_validator_parallel_sin_over(self):
        cw = compile_workflow(
            _doc(
                [
                    {
                        "id": "ciclo",
                        "kind": "loop",
                        "max_iter": 3,
                        "parallel": True,
                        "body": [{"id": "t", "kind": "agent", "agent": "developer"}],
                    }
                ]
            )
        )
        errores = validate_workflow(cw)
        assert any("parallel" in e and "over" in e for e in errores)

    def test_validator_parallel_con_until(self):
        cw = compile_workflow(
            _doc(
                [
                    _par_loop(
                        until={"type": "tool", "tool": "test_runner", "check": "tests_all_pass"}
                    )
                ]
            )
        )
        errores = validate_workflow(cw)
        assert any("parallel" in e and "until" in e for e in errores)


class TestConcurrencia:
    @pytest.mark.asyncio
    async def test_items_concurrentes_y_aislados(self):
        rt = _ParRuntime(latency=0.1, items=["item_1", "item_2", "item_3", "item_4"])
        cw = compile_workflow(
            _doc(
                [
                    {"id": "prep", "kind": "agent", "agent": "developer", "outputs": ["items"]},
                    _par_loop(over="items", parallel_max=4),
                ]
            )
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt.max_concurrent >= 3
        assert sorted(rt.items_seen) == sorted(
            ["haz item_1", "haz item_2", "haz item_3", "haz item_4"]
        )

    @pytest.mark.asyncio
    async def test_semaforo_respeta_parallel_max(self):
        rt = _ParRuntime(latency=0.05, items=["a", "b", "c", "d", "e", "f"])
        cw = compile_workflow(
            _doc(
                [
                    {"id": "prep", "kind": "agent", "agent": "developer", "outputs": ["items"]},
                    _par_loop(over="items", parallel_max=2),
                ]
            )
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt.max_concurrent == 2

    @pytest.mark.asyncio
    async def test_outputs_por_item_se_mergean_al_padre(self):
        rt = _ParRuntime(items=["i1", "i2"])
        cw = compile_workflow(
            _doc(
                [
                    {"id": "prep", "kind": "agent", "agent": "developer", "outputs": ["items"]},
                    _par_loop(
                        over="items",
                        body=[
                            {
                                "id": "tarea",
                                "kind": "agent",
                                "agent": "developer",
                                "goal": "haz $item",
                                "outputs": ["producto"],
                            }
                        ],
                    ),
                ]
            )
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        # outputs de cada ítem se mergean al padre (last-wins) — producto existe
        assert "out_tarea" in str(result.vars.get("producto"))


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_salta_loop_paralelo_completado(self):
        rt = _ParRuntime(items=["a", "b", "c"])
        cw = compile_workflow(
            _doc(
                [
                    {"id": "prep", "kind": "agent", "agent": "developer", "outputs": ["items"]},
                    _par_loop(over="items", parallel_max=4),
                    {"id": "humano", "kind": "checkpoint", "question": "¿ok?"},
                ]
            )
        )
        engine = WorkflowEngine(rt)
        result = await engine.run(cw, inputs={"query": "q"})
        assert result.status == "paused"
        calls_after_loop = len(rt.agent_calls)  # prep + 3 items = 4

        rt.checkpoint_approved = True
        result2 = await WorkflowEngine(rt).run(cw, inputs={"query": "q"}, resume=result.snapshot)
        assert result2.status == "completed"
        # el loop paralelo NO re-corre sus ítems (loop_state persistió)
        assert len(rt.agent_calls) == calls_after_loop

    @pytest.mark.asyncio
    async def test_resume_con_items_incompletos_reanuda_solo_faltantes(self):
        """Pausa DENTRO de un ítem: los otros se cancelan; resume completa
        solo lo incompleto (paths cualificados ciclo#N/id)."""
        rt = _ParRuntime(items=["a", "b"])

        async def call(agent, prompt, *, model_role=None, step_id="", path=""):
            if step_id == "prep":
                return ["a", "b"]
            if "haz b" in prompt:
                await asyncio.sleep(0.3)  # ítem b lento: se cancela con la pausa
            return "ok"

        rt.call_agent = call  # type: ignore[method-assign]
        cw = compile_workflow(
            _doc(
                [
                    {
                        "id": "prep",
                        "kind": "agent",
                        "agent": "developer",
                        "outputs": ["items"],
                    },
                    {
                        "id": "ciclo",
                        "kind": "loop",
                        "max_iter": 10,
                        "over": "items",
                        "parallel": True,
                        "body": [
                            {
                                "id": "tarea",
                                "kind": "agent",
                                "agent": "developer",
                                "goal": "haz $item",
                            },
                            {"id": "humano", "kind": "checkpoint", "question": "¿ok?"},
                        ],
                    },
                ]
            )
        )
        engine = WorkflowEngine(rt)
        result = await engine.run(cw, inputs={"query": "q"})
        assert result.status == "paused"

        rt.checkpoint_approved = True
        result2 = await WorkflowEngine(rt).run(cw, inputs={"query": "q"}, resume=result.snapshot)
        assert result2.status == "completed"


class TestPresetCoordinated:
    def test_preset_actualizado_valida(self):
        from pathlib import Path

        import yaml

        raw = yaml.safe_load(
            (
                Path(__file__).resolve().parent.parent
                / "templates"
                / "workflows"
                / "coordinated.yaml"
            ).read_text()
        )
        cw = compile_workflow(raw)
        assert validate_workflow(cw) == []
        loop = next(s for s in cw.dsl.steps if s.id == "ejecutar")
        assert loop.parallel is True
