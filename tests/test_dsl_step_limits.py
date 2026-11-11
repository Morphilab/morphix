# tests/test_dsl_step_limits.py
"""Límites por-step y recuperación de stalls verificados.

El cap global de 8 iteraciones corta steps de implementación que están a un
test de converger y puede marcar Parcial un run con los tests verdes.
`max_iterations` da margen SOLO a los steps que escriben código; la
recuperación descuenta el stall cuando la verificación del ciclo pasa.
"""

import pytest
from _dsl_engine_support import FakeRuntime, _agent, _compile


class _RuntimeLimits(FakeRuntime):
    """FakeRuntime que registra el override por-step y los recover_steps."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.max_iterations_seen: list[int | None] = []
        self.recovered: list[str] = []

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
    ) -> str:
        self.max_iterations_seen.append(max_iterations)
        return await super().call_agent(  # type: ignore[return-value]
            agent,
            prompt,
            model_role=model_role,
            step_id=step_id,
            path=path,
            extra_context=extra_context,
        )

    def recover_steps(self, prefix: str) -> None:
        self.recovered.append(prefix)


@pytest.mark.asyncio
async def test_max_iterations_llega_al_runtime():
    rt = _RuntimeLimits()
    cw = _compile([_agent("impl", max_iterations=12)])
    from orchestration.dsl.engine import WorkflowEngine

    await WorkflowEngine(rt).run(cw)

    assert rt.max_iterations_seen == [12]


@pytest.mark.asyncio
async def test_sin_max_iterations_no_se_pasa_kwarg():
    rt = _RuntimeLimits()
    cw = _compile([_agent("impl")])
    from orchestration.dsl.engine import WorkflowEngine

    await WorkflowEngine(rt).run(cw)

    assert rt.max_iterations_seen == [None]


def test_max_iterations_fuera_de_rango_rechazado():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _compile([_agent("impl", max_iterations=0)])
    with pytest.raises(ValidationError):
        _compile([_agent("impl", max_iterations=51)])


def _loop_con_veredicto(pasa: bool) -> list[dict]:
    return [
        {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "items"},
        {
            "id": "loop",
            "kind": "loop",
            "max_iter": 1,
            "over": "items",
            "body": [
                _agent("impl"),
                {
                    "id": "tests",
                    "kind": "tool",
                    "tool": "test_runner",
                    "args": {"file_path": "x"},
                    "outputs": {"pasan": "tests_all_pass"},
                    "verdict": "pasan",
                },
            ],
        },
    ]


@pytest.mark.asyncio
async def test_veredicto_verde_recupera_el_stall_del_ciclo():
    rt = _RuntimeLimits(tool_results={"test_runner": {"tests_all_pass": True}})
    cw = _compile(_loop_con_veredicto(pasa=True))
    from orchestration.dsl.engine import WorkflowEngine

    await WorkflowEngine(rt).run(cw)

    assert rt.recovered == ["loop#1/"]


@pytest.mark.asyncio
async def test_veredicto_rojo_no_recupera():
    rt = _RuntimeLimits(tool_results={"test_runner": {"tests_all_pass": False}})
    cw = _compile(_loop_con_veredicto(pasa=False))
    from orchestration.dsl.engine import WorkflowEngine

    await WorkflowEngine(rt).run(cw)

    assert rt.recovered == []


def test_adapter_recupera_solo_el_prefijo_del_ciclo():
    from orchestration.dsl.runtime_adapter import ProductionRuntime

    rt = ProductionRuntime(workspace="main")
    rt.mark_degraded("ciclo#1 ▸ implementar", "ciclo#1/implementar")
    rt.mark_degraded("otro ▸ paso", "otro/paso")

    rt.recover_steps("ciclo#1/")

    assert rt.degraded_steps == ["otro ▸ paso"]
    assert rt.recovered_steps == ["ciclo#1 ▸ implementar"]


def test_scorecard_recuperadas_no_bajan_la_calidad():
    from orchestration.dsl.run_state import BudgetState, PlanItem, RunMemory, RunState
    from orchestration.workflows.orchestrator import _dsl_scorecard

    state = RunState(
        run_id="r",
        goal="g",
        plan=[
            PlanItem(kind="subtask", id=str(i), goal=str(i), path=f"l#{i}", status="delivered")
            for i in range(9)
        ],
        budget=BudgetState(1000, 0, 0, 0),
        replans=[],
        memory=RunMemory(),
    )
    card = _dsl_scorecard(state, tokens=1000, elapsed_s=12.5, recuperadas=2)

    assert card["calidad"] == "Alta"
    assert card["recuperadas"] == 2
    assert card["degradadas"] == 0


@pytest.mark.asyncio
async def test_adapter_cablea_el_override_al_agent_loop(monkeypatch):
    """El override por-step llega como AgentLoopConfig al loop real."""
    from agents.registry import agents_registry
    from orchestration import loop as loop_mod
    from orchestration.dsl.runtime_adapter import ProductionRuntime

    captured: dict = {}

    async def fake_loop(**kwargs):
        captured["config"] = kwargs.get("config")
        return {"status": "completed", "result": "ok", "files_written": [], "iterations": 1}

    monkeypatch.setattr(loop_mod, "execute_agent_loop", fake_loop)
    monkeypatch.setattr(agents_registry, "get_profile", lambda name: {"name": name})

    rt = ProductionRuntime(workspace="main")
    await rt.call_agent("developer", "haz algo", max_iterations=12)
    assert captured["config"] is not None
    assert captured["config"].max_agent_iterations == 12

    await rt.call_agent("developer", "haz algo")
    # sin override el config existe igual (lleva el cap de prompt del run) y
    # el max_agent_iterations cae al global de settings.
    assert captured["config"] is not None
    assert captured["config"].max_agent_iterations == 8
    assert captured["config"].prompt_cap_tokens == 16000


@pytest.mark.asyncio
async def test_max_tokens_per_item_acota_cada_vuelta_secuencial():
    """Una subtarea no puede comerse la vuelta entera: el scope por ítem usa
    min(budget_tokens, max_tokens_per_item)."""
    rt = FakeRuntime(agent_output="ok")
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 3,
                "budget_tokens": 250000,
                "max_tokens_per_item": 90000,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            }
        ]
    )
    from orchestration.dsl.engine import WorkflowEngine

    await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b"]})

    begins = [v for (k, v) in rt.budget_scopes if k == "begin"]
    assert begins == [90000, 90000]
    assert [k for (k, _v) in rt.budget_scopes].count("end") == 2


@pytest.mark.asyncio
async def test_max_tokens_per_item_no_aplica_en_paralelo():
    """El techo compartido del lote no se puede partir por ítem: el campo se
    ignora en parallel (documentado) y se respeta budget_tokens."""
    rt = FakeRuntime(agent_output="ok")
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "parallel": True,
                "max_iter": 2,
                "budget_tokens": 200000,
                "max_tokens_per_item": 50000,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            }
        ]
    )
    from orchestration.dsl.engine import WorkflowEngine

    await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b"]})
    begins = [v for (k, v) in rt.budget_scopes if k == "begin"]
    assert begins and all(v == 200000 for v in begins)


@pytest.mark.asyncio
async def test_agent_step_puede_acotar_su_toolset():
    """Un step de texto declara tools: [] y el runtime recibe lista vacía;
    sin declararlo, el runtime conserva el allowlist del workflow."""
    rt = FakeRuntime(agent_output="ok")
    cw = _compile(
        [
            _agent("texto", tools=[]),
            _agent("normal"),
        ]
    )
    from orchestration.dsl.engine import WorkflowEngine

    await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert rt.agent_tools == [[], None]
