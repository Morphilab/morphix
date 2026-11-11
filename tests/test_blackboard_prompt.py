# tests/test_blackboard_prompt.py
"""Blackboard goal/todo → prompt: el almacén persistente ahora tiene consumidor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.path_resolver import paths
from orchestration.context import plan_mode_active
from orchestration.prompt_budget import enforce_budget
from tools.goal_todo import (
    blackboard_context,
    goal_create,
    goal_update,
    plan_enter,
    todo_write,
)

WS = "blackboard_prompt_test_ws"


def _clean():
    for p in list(paths.memory_dir(WS).rglob("*")):
        if p.is_file():
            p.unlink()


def setup_function():
    _clean()


def teardown_function():
    _clean()


def test_blackboard_vacio_sin_goals_ni_todo():
    assert blackboard_context(WS) == ""


def test_blackboard_renderiza_goals_y_todo_compacto():
    goal = goal_create("Terminar la API de notas", WS, max_rounds=4)
    goal_update(WS, goal["id"], expected_revision=1, status="paused")
    todo_write(
        WS,
        [
            {"text": "implementar el CRUD", "status": "in_progress"},
            {"text": "escribir tests", "status": "pending"},
            {"text": "README", "status": "done"},
        ],
    )

    block = blackboard_context(WS)

    assert block.startswith("<BLACKBOARD>")
    assert block.rstrip().endswith("</BLACKBOARD>")
    assert "Terminar la API de notas" in block
    assert "[paused]" in block
    assert "implementar el CRUD" in block
    assert "escribir tests" in block
    assert "README" in block


def test_blackboard_prioriza_trabajo_abierto_sobre_done():
    todo_write(
        WS,
        [{"text": f"done-{i}", "status": "done"} for i in range(10)]
        + [{"text": "pendiente-único", "status": "pending"}],
    )
    block = blackboard_context(WS, max_chars=1200)
    assert "pendiente-único" in block


def test_blackboard_cap_duro_y_cierre():
    goal_create("G" * 300, WS)
    todo_write(
        WS,
        [{"text": "t" * 200, "status": "pending"} for _ in range(20)],
    )
    block = blackboard_context(WS, max_chars=400)

    assert len(block) <= 400
    assert block.rstrip().endswith("</BLACKBOARD>")


def test_build_blackboard_context_es_best_effort(monkeypatch):
    """Un storage roto no puede tumbar el turno."""
    import tools.goal_todo as gt
    from orchestration.loop import _build_blackboard_context

    def _boom(*a, **k):
        raise OSError("disco lleno")

    monkeypatch.setattr(gt, "blackboard_context", _boom)
    assert _build_blackboard_context(WS) == ""


def test_capas_run_state_y_blackboard_son_propias_y_ordenadas():
    from orchestration.loop import _system_prompt_layers

    layers = _system_prompt_layers(
        react_rules="REGLAS",
        enriched_context="CONTEXTO-FAISS",
        profile_context="PERFIL",
        skills_kits_context="KITS",
        extra_context="<RUN_STATE>estado</RUN_STATE>",
        blackboard_context="<BLACKBOARD>plan</BLACKBOARD>",
    )
    names = [name for name, _text, _cap in layers]
    by_name = {name: text for name, text, _cap in layers}
    caps = {name: cap for name, _text, cap in layers}

    assert by_name["run_state"] == "<RUN_STATE>estado</RUN_STATE>"
    assert by_name["blackboard"] == "<BLACKBOARD>plan</BLACKBOARD>"
    assert "estado" not in by_name["contexto_proyecto"]
    assert names.index("reglas_react") < names.index("run_state") < names.index("blackboard")
    assert names.index("blackboard") < names.index("contexto_proyecto")
    assert caps["run_state"] == 800
    assert caps["blackboard"] == 350


def test_capas_vacias_no_se_crean():
    from orchestration.loop import _system_prompt_layers

    layers = _system_prompt_layers("R", "CTX", "", "")
    names = [name for name, _text, _cap in layers]
    assert "run_state" not in names
    assert "blackboard" not in names


def test_extra_context_gigante_no_desplaza_las_reglas():
    from orchestration.loop import _system_prompt_layers

    out = enforce_budget(
        _system_prompt_layers(
            "REGLAS FIJAS",
            "CTX",
            "",
            "",
            extra_context="estado " * 5000,
            blackboard_context="plan " * 2000,
        ),
        hard_cap=4000,
    )
    assert "REGLAS FIJAS" in out


def test_loop_monta_extra_context_y_blackboard_en_el_prompt():
    """Integración: el extra_context del DSL y el blackboard llegan a las capas."""
    captured: dict = {}

    def _fake_layers(**kwargs):
        captured.update(kwargs)
        return [("reglas_react", "REGLAS", 1200)]

    final = MagicMock()
    final.choices = [MagicMock()]
    final.choices[0].message.content = "ok"
    final.choices[0].message.tool_calls = None
    final.usage = None

    with (
        patch("orchestration.loop.models.call", new_callable=AsyncMock) as mock_llm,
        patch("orchestration.loop._system_prompt_layers", side_effect=_fake_layers),
        patch(
            "orchestration.loop._build_extra_context",
            new=AsyncMock(return_value="CTX"),
        ),
        patch(
            "orchestration.loop._build_blackboard_context",
            return_value="<BLACKBOARD>plan</BLACKBOARD>",
        ),
    ):
        mock_llm.return_value = final
        from orchestration.loop import execute_agent_loop

        asyncio.run(
            execute_agent_loop(
                task="t",
                agent_type="developer",
                history=[],
                workspace="main",
                extra_context="<RUN_STATE>estado</RUN_STATE>",
            )
        )

    assert captured["extra_context"] == "<RUN_STATE>estado</RUN_STATE>"
    assert captured["blackboard_context"] == "<BLACKBOARD>plan</BLACKBOARD>"


def test_blackboard_incluye_plan_activo_y_sus_pasos():
    """El plan de plan-mode se persistía y solo se leía su flag `active`:
    el agente no veía los pasos que él mismo había declarado."""
    plan_enter(WS, "Refactor del parser", ["leer el parser", "escribir tests", "migrar"])
    plan_mode_active.set(True)
    try:
        block = blackboard_context(WS)
    finally:
        plan_mode_active.set(False)

    assert "Refactor del parser" in block
    assert "leer el parser" in block
    assert "migrar" in block


def test_blackboard_no_inyecta_plan_si_el_loop_no_aplico_plan_mode():
    """Paridad con el enforcement: sin `exit_plan_mode` el loop apaga el modo;
    mostrar "Plan activo" sería una fachada (ni reglas ni denegación)."""
    plan_enter(WS, "Refactor del parser", ["leer el parser"])
    plan_mode_active.set(False)

    block = blackboard_context(WS)

    assert "Plan activo" not in block


def test_blackboard_plan_activo_no_ahoga_el_todo():
    """Un plan largo + goals no puede vaciar la sección del trabajo abierto."""
    plan_enter(WS, "Plan enorme", [f"paso {i} " + "x" * 150 for i in range(10)])
    for i in range(3):
        goal_create(f"goal {i} " + "y" * 150, WS)
    todo_write(
        WS,
        [{"text": "pendiente-único", "status": "pending"}]
        + [{"text": f"done-{i} " + "z" * 120, "status": "done"} for i in range(8)],
    )
    plan_mode_active.set(True)
    try:
        block = blackboard_context(WS)
    finally:
        plan_mode_active.set(False)

    assert "Todo (rev" in block
    assert "pendiente-único" in block


def test_blackboard_token_denso_conserva_cierre_bajo_el_cap_de_capa():
    """CJK puede pesar ~2-3 tokens/char: el cap de la capa es en tokens y el
    cierre no puede quedar fuera del prompt aunque el cap sea en tokens."""
    from orchestration.prompt_budget import count_tokens

    goal_create("测试" * 400, WS)
    todo_write(WS, [{"text": "测试" * 300, "status": "pending"}])
    block = blackboard_context(WS)

    assert block.endswith("</BLACKBOARD>")
    assert count_tokens(block) <= 320
    assert len(block) <= 1200


def test_prompt_real_incluye_run_state_y_blackboard_sin_parchear_capas():
    """Integración de punta a punta: el system prompt REAL lleva ambos bloques.

    Sin parchear `_system_prompt_layers`: valida el montaje final (capas +
    enforce_budget) y que el blackboard lea el storage real del workspace.
    """
    goal_create("Meta durable del workspace", WS)
    todo_write(WS, [{"text": "paso durable", "status": "pending"}])

    final = MagicMock()
    final.choices = [MagicMock()]
    final.choices[0].message.content = "ok"
    final.choices[0].message.tool_calls = None
    final.usage = None

    with (
        patch("orchestration.loop.models.call", new_callable=AsyncMock) as mock_llm,
        patch("orchestration.loop._build_extra_context", new=AsyncMock(return_value="CTX")),
    ):
        mock_llm.return_value = final
        from orchestration.loop import execute_agent_loop

        asyncio.run(
            execute_agent_loop(
                task="t",
                agent_type="developer",
                history=[],
                workspace=WS,
                extra_context="<RUN_STATE>estado del run</RUN_STATE>",
            )
        )
        system_content = mock_llm.call_args.kwargs["messages"][0]["content"]

    assert "<RUN_STATE>estado del run</RUN_STATE>" in system_content
    assert "<BLACKBOARD>" in system_content
    assert "Meta durable del workspace" in system_content
    assert "paso durable" in system_content


def test_blackboard_token_denso_con_plan_conserva_el_todo():
    """Con plan+goals CJK, la reserva del todo debe ser también en tokens:
    el fit final recorta desde el final y el todo desaparecía (o quedaba 🟡…)."""
    plan_enter(WS, "计划" * 40, [f"步骤{i}" + "执行" * 40 for i in range(10)])
    for i in range(3):
        goal_create(f"目标{i}" + "完成" * 40, WS)
    todo_write(
        WS,
        [
            {"text": "实现功能", "status": "in_progress"},
            {"text": "编写测试", "status": "pending"},
        ],
    )
    plan_mode_active.set(True)
    try:
        block = blackboard_context(WS)
    finally:
        plan_mode_active.set(False)

    assert "Todo (rev" in block
    assert "🟡 实现功能" in block
    assert block.endswith("</BLACKBOARD>")
