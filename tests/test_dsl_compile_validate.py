# tests/test_dsl_compile_validate.py
"""Tests del compiler YAML→IR y del validador semántico del DSL.

El compiler valida (schema), resuelve includes recursivamente con detección
de ciclos y tope de profundidad, y construye el mapa de paths de nodos.
El validador semántico chequea: agentes/tools permitidos, variables definidas,
depends_on existentes, contratos I/O de includes, until.tool allowlisted.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestration.dsl.compiler import (
    CompiledWorkflow,
    compile_file,
    compile_workflow,
)
from orchestration.dsl.validator import validate_workflow


def _doc(steps: list[dict], **over) -> dict:
    base = {
        "name": "demo",
        "agents": {"allowed": ["developer", "analista", "moderador"]},
        "tools": {"allowed": ["file_manager", "test_runner"]},
        "steps": steps,
    }
    base.update(over)
    return base


def _agent(sid: str, agent: str = "developer", **over) -> dict:
    step = {"id": sid, "kind": "agent", "agent": agent, "goal": "haz"}
    step.update(over)
    return step


class FakeCatalog:
    """Catálogo de presets para tests: dict nombre → documento raw."""

    def __init__(self, presets: dict[str, dict]):
        self.presets = presets

    def load(self, name: str) -> dict | None:
        return self.presets.get(name)


class TestCompiler:
    def test_compile_minimal(self):
        cw = compile_workflow(_doc([_agent("paso1")]))
        assert isinstance(cw, CompiledWorkflow)
        assert cw.dsl.steps[0].id == "paso1"

    def test_compile_rejects_invalid_schema(self):
        with pytest.raises(ValidationError):
            compile_workflow(_doc([{"id": "x", "kind": "magia"}]))

    def test_node_paths_nested(self):
        cw = compile_workflow(
            _doc(
                [
                    _agent("inicio"),
                    {
                        "id": "ciclo",
                        "kind": "loop",
                        "max_iter": 3,
                        "body": [_agent("dentro")],
                    },
                ]
            )
        )
        assert cw.node_paths["inicio"] == "inicio"
        assert cw.node_paths["dentro"] == "ciclo ▸ dentro"

    def test_node_paths_parallel_and_decide(self):
        cw = compile_workflow(
            _doc(
                [
                    {
                        "id": "par",
                        "kind": "parallel",
                        "branches": [{"steps": [_agent("x1")]}],
                    },
                    {
                        "id": "elegir",
                        "kind": "decide",
                        "question": "¿cual?",
                        "options": ["a", "b"],
                        "fallback": "a",
                        "branches": {"a": [_agent("ea")], "b": [_agent("eb")]},
                    },
                ]
            )
        )
        assert cw.node_paths["x1"] == "par ▸ x1"
        assert cw.node_paths["ea"] == "elegir ▸ a ▸ ea"

    def test_include_resolved(self):
        child = _doc([_agent("hijo_paso")], name="hijo")
        parent = _doc(
            [
                {
                    "id": "sub",
                    "kind": "workflow",
                    "name": "hijo",
                    "inputs": {},
                    "outputs": [],
                }
            ]
        )
        cw = compile_workflow(parent, catalog=FakeCatalog({"hijo": child}))
        assert "hijo" in cw.includes
        assert cw.includes["hijo"].dsl.steps[0].id == "hijo_paso"
        assert cw.node_paths["sub"] == "sub"

    def test_include_missing_preset_fails(self):
        parent = _doc([{"id": "sub", "kind": "workflow", "name": "inexistente"}])
        with pytest.raises(ValueError, match="inexistente"):
            compile_workflow(parent, catalog=FakeCatalog({}))

    def test_include_cycle_fails(self):
        a = _doc([{"id": "sub", "kind": "workflow", "name": "b"}], name="a")
        b = _doc([{"id": "sub", "kind": "workflow", "name": "a"}], name="b")
        with pytest.raises(ValueError, match="[Cc]iclo"):
            compile_workflow(a, catalog=FakeCatalog({"a": a, "b": b}))

    def test_include_depth_limit(self):
        docs = {}
        for i in range(5):
            docs[f"w{i}"] = _doc(
                [{"id": "sub", "kind": "workflow", "name": f"w{i + 1}"}],
                name=f"w{i}",
            )
        docs["w5"] = _doc([_agent("final")], name="w5")
        with pytest.raises(ValueError, match="profundidad"):
            compile_workflow(docs["w0"], catalog=FakeCatalog(docs))

    def test_compile_file(self, tmp_path: Path):
        import yaml

        raw = _doc([_agent("desde_archivo")])
        raw["version"] = 1
        p = tmp_path / "demo.yaml"
        p.write_text(yaml.safe_dump(raw), encoding="utf-8")
        cw = compile_file(p)
        assert cw.dsl.steps[0].id == "desde_archivo"


class TestValidadorSemantico:
    def test_agent_no_permitido(self):
        cw = compile_workflow(_doc([_agent("paso1", agent="intruso")]))
        errores = validate_workflow(cw)
        assert any("paso1" in e and "intruso" in e for e in errores)

    def test_tool_no_permitida(self):
        cw = compile_workflow(
            _doc(
                [
                    {
                        "id": "t1",
                        "kind": "tool",
                        "tool": "bash_manager",
                        "args": {},
                    }
                ]
            )
        )
        errores = validate_workflow(cw)
        assert any("bash_manager" in e for e in errores)

    def test_until_tool_must_be_allowed(self):
        cw = compile_workflow(
            _doc(
                [
                    {
                        "id": "ciclo",
                        "kind": "loop",
                        "max_iter": 3,
                        "until": {
                            "type": "tool",
                            "tool": "git_manager",
                            "check": "ok",
                        },
                        "body": [_agent("dentro")],
                    }
                ]
            )
        )
        errores = validate_workflow(cw)
        assert any("git_manager" in e and "until" in e for e in errores)

    def test_variable_indefinida_en_goal(self):
        cw = compile_workflow(_doc([_agent("paso1", goal="usa $inexistente")]))
        errores = validate_workflow(cw)
        assert any("$inexistente" in e for e in errores)

    def test_variables_reservadas_ok(self):
        cw = compile_workflow(_doc([_agent("paso1", goal="responde a $query y usa $item")]))
        assert validate_workflow(cw) == []

    def test_variable_definida_por_step_anterior(self):
        cw = compile_workflow(
            _doc([_agent("paso1", outputs=["spec"]), _agent("paso2", goal="usa $spec")])
        )
        assert validate_workflow(cw) == []

    def test_variable_definida_por_input(self):
        cw = compile_workflow(
            _doc([_agent("paso1", goal="usa $requerimiento")], inputs=["requerimiento"])
        )
        assert validate_workflow(cw) == []

    def test_workflow_inputs_binding_undefined_var(self):
        child = _doc([_agent("hijo")], name="hijo", inputs=["datos"], outputs=["res"])
        parent = _doc(
            [
                {
                    "id": "sub",
                    "kind": "workflow",
                    "name": "hijo",
                    "inputs": {"datos": "$no_existe"},
                    "outputs": ["res"],
                }
            ]
        )
        cw = compile_workflow(parent, catalog=FakeCatalog({"hijo": child}))
        errores = validate_workflow(cw)
        assert any("$no_existe" in e for e in errores)

    def test_workflow_inputs_contract_mismatch(self):
        # el hijo no declara input "datos" → binding inválido
        child = _doc([_agent("hijo")], name="hijo")
        parent = _doc(
            [
                {
                    "id": "sub",
                    "kind": "workflow",
                    "name": "hijo",
                    "inputs": {"datos": "$query"},
                    "outputs": [],
                }
            ]
        )
        cw = compile_workflow(parent, catalog=FakeCatalog({"hijo": child}))
        errores = validate_workflow(cw)
        assert any("datos" in e and "hijo" in e for e in errores)

    def test_depends_on_desconocido(self):
        cw = compile_workflow(
            _doc(
                [
                    {
                        "id": "par",
                        "kind": "parallel",
                        "branches": [{"steps": [_agent("x1")], "depends_on": ["fantasma"]}],
                    }
                ]
            )
        )
        errores = validate_workflow(cw)
        assert any("fantasma" in e for e in errores)

    def test_depends_on_valido_cross_branch(self):
        cw = compile_workflow(
            _doc(
                [
                    {
                        "id": "par",
                        "kind": "parallel",
                        "branches": [
                            {"steps": [_agent("x1")]},
                            {"steps": [_agent("x2")], "depends_on": ["x1"]},
                        ],
                    }
                ]
            )
        )
        assert validate_workflow(cw) == []

    def test_aggregate_moderator_agent_no_permitido(self):
        cw = compile_workflow(
            _doc(
                [
                    _agent("paso1"),
                    {
                        "id": "agg",
                        "kind": "aggregate",
                        "strategy": "moderator",
                        "agent": "moderador",
                    },
                ]
            )
        )
        assert validate_workflow(cw) == []

    def test_aggregate_moderator_sin_agente(self):
        cw = compile_workflow(_doc([{"id": "agg", "kind": "aggregate", "strategy": "moderator"}]))
        errores = validate_workflow(cw)
        assert any("moderator" in e.lower() for e in errores)

    def test_valido_completo_sin_errores(self):
        cw = compile_workflow(
            _doc(
                [
                    _agent("spec", agent="analista", outputs=["spec"]),
                    {
                        "id": "ciclo",
                        "kind": "loop",
                        "max_iter": 5,
                        "over": "subtasks",
                        "until": {
                            "type": "tool",
                            "tool": "test_runner",
                            "check": "tests_all_pass",
                        },
                        "body": [_agent("impl", goal="implementa $item con $spec")],
                    },
                    {
                        "id": "agg",
                        "kind": "aggregate",
                        "strategy": "moderator",
                        "agent": "moderador",
                    },
                ],
                outputs=["res"],
            )
        )
        # over: "subtasks" no está definida → debe reportarse
        errores = validate_workflow(cw)
        assert any("subtasks" in e for e in errores)
