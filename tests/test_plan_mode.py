# tests/test_plan_mode.py
"""plan-mode real: ContextVar, denegación de escrituras y persistencia."""

import pytest
from _agent_loop_support import mock_agent_deps  # noqa: E402,F401 — fixture compartida

from core.path_resolver import paths
from orchestration.context import plan_mode_active
from tools.goal_todo import _exit_plan_mode_tool, _plan_mode_tool

WS = "plan_mode_test_ws"


def _clean():
    for p in list(paths.memory_dir(WS).rglob("*")):
        if p.is_file():
            p.unlink()


@pytest.mark.asyncio
async def test_plan_mode_activa_y_exit_apaga():
    _clean()
    try:
        plan_mode_active.set(False)
        await _plan_mode_tool(title="Plan de prueba", steps=["a"], workspace=WS)
        assert plan_mode_active.get() is True
        await _exit_plan_mode_tool(decision="Approve", workspace=WS)
        assert plan_mode_active.get() is False
    finally:
        plan_mode_active.set(False)
        _clean()


@pytest.mark.asyncio
async def test_loop_deniega_escritura_con_plan_activo():
    from orchestration.loop import _execute_single_tool_call

    plan_mode_active.set(True)
    try:
        out, _mod, _path, ok = await _execute_single_tool_call(
            "file_manager",
            {"action": "write", "path": "x.py", "content": "x"},
            project_root=None,
            workspace="main",
        )
    finally:
        plan_mode_active.set(False)
    assert ok is False
    assert "plan" in out.lower()


@pytest.mark.asyncio
async def test_loop_arranca_en_plan_mode_si_plan_json_activo(mock_agent_deps, monkeypatch):
    from orchestration.loop import execute_agent_loop

    monkeypatch.setattr("tools.goal_todo.plan_get", lambda ws: {"active": True, "steps": []})
    mock_llm, _mock_tool, _mock_response = mock_agent_deps
    captured: dict = {}

    async def capture(messages, **kw):
        captured["messages"] = messages
        return _mock_response

    mock_llm.side_effect = capture
    await execute_agent_loop(task="hola", agent_type="conversacional", workspace="main")
    system = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "PLAN MODE" in system


def test_developer_declara_plan_mode():
    from pathlib import Path

    import yaml

    doc = yaml.safe_load(
        (
            Path(__file__).resolve().parent.parent / "templates" / "agents" / "developer.yaml"
        ).read_text(encoding="utf-8")
    )
    assert {"plan_mode", "exit_plan_mode"} <= set(doc["tools"])


@pytest.mark.asyncio
async def test_plan_activo_sin_exit_plan_mode_no_bloquea(mock_agent_deps, monkeypatch):
    """Un plan huérfano no puede dejar sin escritura a un preset que no puede salir."""
    from orchestration.loop import execute_agent_loop

    monkeypatch.setattr("tools.goal_todo.plan_get", lambda ws: {"active": True, "steps": []})
    plan_mode_active.set(False)
    await execute_agent_loop(
        task="hola",
        agent_type="developer",
        workspace="main",
        allowed_tools=["file_manager"],
    )
    assert plan_mode_active.get() is False


@pytest.mark.asyncio
async def test_plan_mode_deniega_remove():
    from orchestration.loop import _execute_single_tool_call

    plan_mode_active.set(True)
    try:
        out, _mod, _path, ok = await _execute_single_tool_call(
            "file_manager",
            {"action": "remove", "path": "x.py"},
            project_root=None,
            workspace="main",
        )
    finally:
        plan_mode_active.set(False)
    assert ok is False
    assert "plan" in out.lower()


@pytest.mark.asyncio
async def test_plan_mode_deniega_bash():
    from orchestration.loop import _execute_single_tool_call

    plan_mode_active.set(True)
    try:
        out, _mod, _path, ok = await _execute_single_tool_call(
            "bash_manager", {"command": "ls"}, project_root=None, workspace="main"
        )
    finally:
        plan_mode_active.set(False)
    assert ok is False
    assert "bash" in out.lower()


def test_plan_mode_layer_va_antes_del_contexto_y_sobrevive():
    """La notificación no puede ser la primera capa recortada por presupuesto."""
    from orchestration.loop import _system_prompt_layers
    from orchestration.prompt_budget import enforce_budget

    layers = _system_prompt_layers("R" * 100, "C" * 5000, "P" * 500, "S" * 500, plan_mode=True)
    names = [n for n, _, _ in layers]
    assert names.index("plan_mode") == 1
    out = enforce_budget(layers, hard_cap=150)
    assert "PLAN MODE" in out


def test_loop_no_reintroduce_instrucciones_con_plan_mode_hardcodeado():
    """El `tool_instructions_text` muerto tenía plan_mode=False hardcodeado:
    la fachada que la tesis señaló (nadie leía esa variable). Guard source:
    el loop no vuelve a montar instrucciones textuales con un flag fijo."""
    import inspect

    from orchestration import loop

    src = inspect.getsource(loop)
    assert "tool_instructions_text" not in src
    assert "plan_mode=False" not in src
