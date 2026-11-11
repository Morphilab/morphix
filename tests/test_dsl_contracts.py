# tests/test_dsl_contracts.py
"""Contrato produces/verified_by/min_output_chars: evidencia, no prosa."""

import pytest
from _dsl_engine_support import FakeRuntime, _compile, _doc

from orchestration.dsl.compiler import compile_workflow
from orchestration.dsl.engine import WorkflowEngine
from orchestration.dsl.schema import WorkflowDSL
from orchestration.dsl.validator import validate_workflow


def test_schema_acepta_produces_y_verified_by():
    doc = _doc(
        [
            {
                "id": "sub",
                "kind": "agent",
                "agent": "developer",
                "goal": "x",
                "produces": ["tests/test_subtarea_$iter.py"],
                "verified_by": "ver",
            },
            {
                "id": "ver",
                "kind": "tool",
                "tool": "test_runner",
                "args": {"file_path": "tests/test_subtarea_$iter.py"},
                "outputs": {"pasan": "tests_all_pass"},
                "verdict": "pasan",
            },
        ]
    )
    WorkflowDSL.model_validate(doc)


def test_validator_rechaza_verified_by_inexistente_o_no_tool():
    cw = compile_workflow(
        _doc(
            [
                {
                    "id": "sub",
                    "kind": "agent",
                    "agent": "developer",
                    "goal": "x",
                    "verified_by": "nope",
                }
            ]
        )
    )
    errors = validate_workflow(cw)
    assert any("verified_by" in e for e in errors), errors


def test_validator_rechaza_on_blocker_stop_sin_gate():
    cw = compile_workflow(
        _doc(
            [
                {
                    "id": "sub",
                    "kind": "agent",
                    "agent": "developer",
                    "goal": "x",
                    "on_blocker": "stop",
                }
            ]
        )
    )
    errors = validate_workflow(cw)
    assert any("on_blocker" in e for e in errors), errors


@pytest.mark.asyncio
async def test_agente_sin_archivo_queda_unmeasured():
    rt = FakeRuntime(agent_output="plan en prosa")
    cw = _compile(
        [
            {
                "id": "sub",
                "kind": "agent",
                "agent": "developer",
                "goal": "x",
                "produces": ["tests/test_x.py"],
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    item = res.run_state["plan"][0]
    assert item["status"] == "unmeasured"


@pytest.mark.asyncio
async def test_verificacion_verde_marca_delivered():
    rt = FakeRuntime(agent_output="ok", files_written=["tests/test_x.py"])
    rt.tool_results["test_runner"] = {"tests_all_pass": True, "passed_count": 3}
    cw = _compile(
        [
            {
                "id": "sub",
                "kind": "agent",
                "agent": "developer",
                "goal": "x",
                "produces": ["tests/test_x.py"],
                "verified_by": "ver",
            },
            {
                "id": "ver",
                "kind": "tool",
                "tool": "test_runner",
                "args": {"file_path": "tests/test_x.py"},
                "outputs": {"pasan": "tests_all_pass"},
                "verdict": "pasan",
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    item = res.run_state["plan"][0]
    assert item["status"] == "delivered"
    assert item["verification"]["passed"] is True


@pytest.mark.asyncio
async def test_verificacion_roja_marca_failed():
    rt = FakeRuntime(agent_output="ok", files_written=["tests/test_x.py"])
    rt.tool_results["test_runner"] = {"tests_all_pass": False, "failed_count": 2}
    cw = _compile(
        [
            {
                "id": "sub",
                "kind": "agent",
                "agent": "developer",
                "goal": "x",
                "produces": ["tests/test_x.py"],
                "verified_by": "ver",
            },
            {
                "id": "ver",
                "kind": "tool",
                "tool": "test_runner",
                "args": {"file_path": "tests/test_x.py"},
                "outputs": {"pasan": "tests_all_pass"},
                "verdict": "pasan",
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    item = res.run_state["plan"][0]
    assert item["status"] == "failed"
    assert item["verification"]["passed"] is False


@pytest.mark.asyncio
async def test_min_output_chars_marca_unmeasured():
    rt = FakeRuntime(agent_output="corto")
    cw = _compile(
        [
            {
                "id": "spec",
                "kind": "agent",
                "agent": "developer",
                "goal": "spec",
                "min_output_chars": 100,
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.run_state["plan"][0]["status"] == "unmeasured"


@pytest.mark.asyncio
async def test_until_de_un_loop_sin_over_es_la_verificacion_del_item():
    """tdd/edd: la feature es UN subtask y su verificación ES el until."""
    rt = FakeRuntime(agent_output="ok", files_written=["x.py"])
    rt.tool_results["test_runner"] = {"tests_all_pass": True, "passed_count": 2}
    cw = _compile(
        [
            {
                "id": "ciclo",
                "kind": "loop",
                "max_iter": 2,
                "until": {
                    "type": "tool",
                    "tool": "test_runner",
                    "check": "tests_all_pass",
                    "args": {"file_path": "tests/x.py"},
                },
                "body": [
                    {
                        "id": "impl",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "impl",
                        "produces": ["x.py"],
                    }
                ],
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    item = next(p for p in res.run_state["plan"] if p["id"] == "ciclo")
    assert item["verification"]["tool"] == "test_runner"
    assert item["verification"]["passed"] is True
    assert item["verification"]["counts"]["passed_count"] == 2


@pytest.mark.asyncio
async def test_tool_step_registra_la_firma_en_run_memory():
    rt = FakeRuntime(agent_output="ok", files_written=["x.py"])
    rt.tool_results["test_runner"] = {"tests_all_pass": True, "passed_count": 1}
    cw = _compile(
        [
            {
                "id": "sub",
                "kind": "agent",
                "agent": "developer",
                "goal": "x",
                "produces": ["x.py"],
                "verified_by": "ver",
            },
            {
                "id": "ver",
                "kind": "tool",
                "tool": "test_runner",
                "args": {"file_path": "tests/x.py"},
                "outputs": {"pasan": "tests_all_pass"},
                "verdict": "pasan",
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    tried = res.run_state["memory"]["tried"]
    assert any("test_runner|tests/x.py" in sigs for sigs in tried.values())


def test_validator_rechaza_produces_en_parallel():
    """Un step de rama no materializa item: el contrato se perdería."""
    cw = compile_workflow(
        _doc(
            [
                {
                    "id": "par",
                    "kind": "parallel",
                    "branches": [
                        {
                            "steps": [
                                {
                                    "id": "a",
                                    "kind": "agent",
                                    "agent": "developer",
                                    "goal": "x",
                                    "produces": ["x.py"],
                                }
                            ]
                        }
                    ],
                }
            ]
        )
    )
    errors = validate_workflow(cw)
    assert any("parallel/decide" in e for e in errors), errors


@pytest.mark.asyncio
async def test_produces_con_doble_asterisco_matchea_hijos_directos():
    rt = FakeRuntime(agent_output="ok", files_written=["src/main.py"])
    cw = _compile(
        [
            {
                "id": "a",
                "kind": "agent",
                "agent": "developer",
                "goal": "x",
                "produces": ["src/**/*.py"],
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.run_state["plan"][0]["status"] == "delivered"


@pytest.mark.asyncio
async def test_produces_no_se_come_los_puntos_de_un_directorio_oculto():
    rt = FakeRuntime(agent_output="ok", files_written=[".config/x.py"])
    cw = _compile(
        [
            {
                "id": "a",
                "kind": "agent",
                "agent": "developer",
                "goal": "x",
                "produces": [".config/x.py"],
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.run_state["plan"][0]["status"] == "delivered"
