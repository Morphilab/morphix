# tests/test_dsl_replan.py
"""ReplanPolicy + ReplanDecision: schema, validación y runtime JSON."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _dsl_engine_support import _doc

from orchestration.dsl.compiler import compile_workflow
from orchestration.dsl.runtime_adapter import ProductionRuntime
from orchestration.dsl.schema import WorkflowDSL
from orchestration.dsl.validator import validate_workflow


def _completion(content: str):
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = content
    return completion


def test_schema_replan_solo_en_loops_secuenciales_con_over():
    doc = _doc(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "replan": {"max": 1, "max_new_items": 2},
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            }
        ]
    )
    WorkflowDSL.model_validate(doc)

    cw = compile_workflow(
        _doc(
            [
                {
                    "id": "l",
                    "kind": "loop",
                    "max_iter": 2,
                    "replan": {"max": 1},
                    "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "x"}],
                }
            ]
        )
    )
    errors = validate_workflow(cw)
    assert any("replan" in e for e in errors), errors


@pytest.mark.asyncio
async def test_runtime_replan_parsea_json_valido_y_falla_a_stop():
    rt = ProductionRuntime(workspace="main")
    with patch("llm.models.call", new_callable=AsyncMock) as call:
        call.return_value = _completion('{"action":"replace","items":[{"goal":"b"}],"reason":"x"}')
        decision = await rt.replan(
            goal="g",
            plan=[],
            failed_item=None,
            evidence={},
            remaining_tokens=50000,
            max_new_items=2,
        )
    assert decision.action == "replace"
    assert decision.items[0].goal == "b"

    with patch("llm.models.call", new_callable=AsyncMock) as call:
        call.return_value = _completion("no json")
        decision = await rt.replan(
            goal="g",
            plan=[],
            failed_item=None,
            evidence={},
            remaining_tokens=50000,
            max_new_items=2,
        )
    assert decision.action == "stop"
    assert "inválido" in decision.reason.lower() or "json" in decision.reason.lower()


def test_replan_policy_default_incluye_budget():
    from orchestration.dsl.schema import ReplanPolicy

    assert ReplanPolicy().triggers == ["verification_failed", "unmeasured", "budget"]


@pytest.mark.asyncio
async def test_replan_tolera_campos_extra_y_razon_larga():
    """Un JSON con ruido (campos extra, razón >500) no puede bloquear el run:
    el boundary sanea y la decisión se aplica."""
    rt = ProductionRuntime(workspace="main")
    payload = (
        '{"action":"replace","items":[{"goal":"b","extra":1}],'
        '"skipped":[{"item_id":"1","reason":"ok","extra":2}],'
        '"reason":"' + "x" * 600 + '","notes":"ignorame"}'
    )
    with patch("llm.models.call", new_callable=AsyncMock) as call:
        call.return_value = _completion(payload)
        decision = await rt.replan(
            goal="g",
            plan=[],
            failed_item=None,
            evidence={},
            remaining_tokens=50000,
            max_new_items=2,
        )
    assert decision.action == "replace"
    assert decision.items[0].goal == "b"
    assert len(decision.reason) == 500
