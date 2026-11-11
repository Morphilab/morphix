# tests/test_dsl_stop.py
"""Stop terminal: un gate con on_blocker=stop detiene el run."""

import pytest
from _dsl_engine_support import FakeRuntime, _compile

from orchestration.dsl.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_gate_con_on_blocker_stop_detiene_el_run():
    rt = FakeRuntime(agent_output="CONTRADICCION: HU-A vs HU-B: mismo input")
    cw = _compile(
        [
            {
                "id": "rev",
                "kind": "agent",
                "agent": "analista",
                "goal": "revisa",
                "gate": "CONTRADICCION:",
                "on_blocker": "stop",
            },
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b"]})
    assert res.status == "stopped"
    assert "CONTRADICCION" in (res.stop_reason or "")
    assert len(rt.agent_calls) == 1  # el loop no corrió


@pytest.mark.asyncio
async def test_stop_dentro_de_un_loop_es_terminal_no_failed():
    """El stop de un step anidado no puede degradar a fallo técnico."""
    rt = FakeRuntime(agent_output="CONTRADICCION: HU-1 vs HU-2")
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "body": [
                    {
                        "id": "rev",
                        "kind": "agent",
                        "agent": "analista",
                        "goal": "revisa $item",
                        "gate": "(?i)contradicci[oó]n\\s*:",
                        "on_blocker": "stop",
                    }
                ],
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b"]})
    assert res.status == "stopped"
    assert len(rt.agent_calls) == 1


def test_el_gate_de_bdd_acepta_acentos_y_mayusculas():
    import re
    from pathlib import Path

    import yaml

    doc = yaml.safe_load(Path("templates/workflows/bdd.yaml").read_text(encoding="utf-8"))
    step = next(s for s in doc["steps"] if s["id"] == "revisar_historias")
    assert re.search(step["gate"], "Contradicción: HU-1 vs HU-2")
    assert re.search(step["gate"], "CONTRADICCION: HU-1 vs HU-2")
