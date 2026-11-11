# tests/test_dsl_agent_outcome.py
"""AgentOutcome: el motor recibe status, archivos y tokens, no solo texto."""

from unittest.mock import AsyncMock, patch

import pytest

from orchestration.dsl.engine import AgentOutcome, _as_outcome
from orchestration.dsl.runtime_adapter import ProductionRuntime


def test_as_outcome_acepta_str_y_agent_outcome():
    assert _as_outcome("hola").status == "completed"
    assert _as_outcome("hola").text == "hola"
    out = AgentOutcome("stalled", "x", ["a.py"], 8, 100)
    assert _as_outcome(out) is out


@pytest.mark.asyncio
async def test_call_agent_estructura_el_resultado_del_loop():
    rt = ProductionRuntime(workspace="main")
    with (
        patch("agents.registry.agents_registry.get_profile", return_value={"tools": []}),
        patch("orchestration.loop.execute_agent_loop", new_callable=AsyncMock) as loop,
    ):
        loop.return_value = {
            "status": "stalled",
            "result": "texto",
            "files_written": ["a.py"],
            "iterations": 8,
            "tokens_used_call": 123,
        }
        out = await rt.call_agent("developer", "haz algo")
    assert isinstance(out, AgentOutcome)
    assert out.status == "stalled"
    assert out.text.startswith("texto")
    assert out.files_written == ["a.py"]
    assert out.iterations == 8
    assert out.tokens == 123
