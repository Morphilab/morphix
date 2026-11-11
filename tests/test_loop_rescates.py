# tests/test_loop_rescates.py — honestidad del ciclo de respuesta del agent-loop
"""Contratos del cierre de turno del agent-loop: el verde de la última
verificación sobrevive entre iteraciones (los rescates del final vacío y del
tope de iteraciones viven), el repr del completion crudo jamás es respuesta,
y un agente inexistente falla loud en el runtime DSL."""

import asyncio  # noqa: F401
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402
from _agent_loop_support import mock_agent_deps  # noqa: E402,F401 — fixture compartida del split


def _tool_response(name: str = "test_runner", args: str = '{"file_path": "tests/test_x.py"}'):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = None
    tc = MagicMock()
    tc.function.name = name
    tc.function.arguments = args
    resp.choices[0].message.tool_calls = [tc]
    return resp


def _empty_response():
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = ""
    resp.choices[0].message.tool_calls = None
    return resp


@pytest.mark.asyncio
async def test_verde_persiste_hasta_el_final_vacio_nativo(mock_agent_deps):
    """Un test_runner en verde (iter 1) seguido de un final VACÍO (iter 2):
    la evidencia del verde es el resultado — el rescate del final vacío
    viva. El nudge del final vacío NO consume la segunda iteración cuando
    el verde la respalda."""
    mock_llm, mock_tool, _ = mock_agent_deps
    mock_tool.return_value = {"output": "3 passed, 0 failed", "success": True}
    # tres respuestas: si el rescate falla, el nudge de la iter 2 pide una 3ª
    mock_llm.side_effect = [_tool_response(), _empty_response(), _empty_response()]

    from orchestration.loop import execute_agent_loop

    result = await execute_agent_loop(
        task="Arregla el test",
        agent_type="developer",
        allowed_tools=["test_runner"],
        workspace="main",
    )
    assert (
        "Verificación en verde" in result["result"]
    ), "el verde del lote de tools anterior debe rescatar el final vacío"
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_final_vacio_texto_no_deja_repr(mock_agent_deps):
    """Modo texto con content vacío: la respuesta final es honesta (vacía),
    jamás el repr del objeto completion crudo."""
    mock_llm, _, _ = mock_agent_deps
    mock_llm.return_value = _empty_response()

    from orchestration.loop import execute_agent_loop

    result = await execute_agent_loop(
        task="algo",
        agent_type="developer",
        allowed_tools=[],
        workspace="main",
    )
    assert "MagicMock" not in result["result"], "el repr del completion crudo no es una respuesta"
    assert result["result"] == ""


# ── Agente inexistente falla loud en el runtime DSL ────────────────────────


@pytest.mark.asyncio
async def test_agente_inexistente_falla_loud(monkeypatch):
    """Un typo en `agent:` sobrevivía compile+validate+run corriendo con el
    prompt ReAct genérico (get_profile None sin raise): el runtime DSL es la
    frontera que valida contra el registry y falla accionable — el validador
    NO se toca (su invariante documentada: nunca consulta registros vivos)."""
    import agents.registry as reg
    from orchestration.dsl.engine import EngineError
    from orchestration.dsl.runtime_adapter import ProductionRuntime

    monkeypatch.setattr(
        reg.agents_registry,
        "get_profile",
        lambda name: None if name == "fantasma" else object(),
    )
    monkeypatch.setattr(
        "orchestration.loop.execute_agent_loop",
        AsyncMock(return_value={"status": "completed", "result": "x", "files_written": []}),
    )

    rt = ProductionRuntime(workspace="main", project_root=None, tools_allowed=[], query="q")
    with pytest.raises(EngineError) as ei:
        await rt.call_agent("fantasma", "haz algo")
    assert "fantasma" in str(ei.value), "el error nombra al agente"


@pytest.mark.asyncio
async def test_agente_registrado_pasa_al_loop(monkeypatch):
    """Con perfil presente el gate es transparente: el loop corre normal."""
    import agents.registry as reg
    from orchestration.dsl.runtime_adapter import ProductionRuntime

    monkeypatch.setattr(reg.agents_registry, "get_profile", lambda name: object())
    monkeypatch.setattr(
        "orchestration.loop.execute_agent_loop",
        AsyncMock(
            return_value={
                "status": "completed",
                "result": "hecho",
                "files_written": [],
                "iterations": 1,
            }
        ),
    )

    rt = ProductionRuntime(workspace="main", project_root=None, tools_allowed=[], query="q")
    out = await rt.call_agent("developer", "haz algo")
    assert out.text == "hecho"
    assert out.status == "completed"
