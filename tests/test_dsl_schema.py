# tests/test_dsl_schema.py
"""Tests del schema DSL de workflows (orchestration/dsl/schema.py).

Contrato structural: version, discriminated union de steps por `kind`,
ids únicos recursivos, bounds de loop, until tipado, decide con enum+fallback,
deny-by-default de agents/tools. La validación semántica (agentes/tools
existentes, grafo, includes) vive en validator.py.
"""

import pytest
from pydantic import ValidationError

from orchestration.dsl.schema import WorkflowDSL


def _minimal_doc() -> dict:
    return {
        "name": "demo",
        "steps": [{"id": "paso1", "kind": "agent", "agent": "developer", "goal": "haz algo"}],
    }


class TestDocumentoRaiz:
    def test_minimal_valid(self):
        doc = WorkflowDSL.model_validate(_minimal_doc())
        assert doc.steps[0].kind == "agent"
        assert doc.version == 1

    def test_unknown_root_field_rejected(self):
        raw = _minimal_doc()
        raw["campo_inventado"] = True
        with pytest.raises(ValidationError, match="campo_inventado"):
            WorkflowDSL.model_validate(raw)

    def test_version_2_rejected(self):
        raw = _minimal_doc()
        raw["version"] = 2
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate(raw)

    def test_steps_empty_rejected(self):
        raw = _minimal_doc()
        raw["steps"] = []
        with pytest.raises(ValidationError, match="steps"):
            WorkflowDSL.model_validate(raw)

    def test_unknown_step_kind_rejected(self):
        raw = _minimal_doc()
        raw["steps"] = [{"id": "x", "kind": "teletransporte"}]
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate(raw)

    def test_unknown_step_field_rejected(self):
        raw = _minimal_doc()
        raw["steps"][0]["magia"] = 1
        with pytest.raises(ValidationError, match="magia"):
            WorkflowDSL.model_validate(raw)

    def test_inputs_outputs_identifiers(self):
        raw = _minimal_doc()
        raw["inputs"] = ["requerimiento"]
        raw["outputs"] = ["spec", "codigo"]
        doc = WorkflowDSL.model_validate(raw)
        assert doc.inputs == ["requerimiento"]

    def test_inputs_bad_identifier_rejected(self):
        raw = _minimal_doc()
        raw["inputs"] = ["1mal-nombre"]
        with pytest.raises(ValidationError, match="inputs"):
            WorkflowDSL.model_validate(raw)


class TestIdsYAnidamiento:
    def test_duplicate_ids_rejected(self):
        raw = _minimal_doc()
        raw["steps"].append({"id": "paso1", "kind": "agent", "agent": "analista"})
        with pytest.raises(ValidationError, match="paso1"):
            WorkflowDSL.model_validate(raw)

    def test_duplicate_ids_nested_rejected(self):
        raw = _minimal_doc()
        raw["steps"].append(
            {
                "id": "ciclo",
                "kind": "loop",
                "max_iter": 3,
                "body": [{"id": "paso1", "kind": "agent", "agent": "developer"}],
            }
        )
        with pytest.raises(ValidationError, match="paso1"):
            WorkflowDSL.model_validate(raw)


class TestLoopStep:
    def _loop(self, **over) -> dict:
        base = {
            "id": "ciclo",
            "kind": "loop",
            "max_iter": 5,
            "body": [{"id": "dentro", "kind": "agent", "agent": "developer"}],
        }
        base.update(over)
        return base

    def test_counter_loop_valid(self):
        doc = WorkflowDSL.model_validate({**_minimal_doc(), "steps": [self._loop()]})
        assert doc.steps[0].max_iter == 5

    def test_max_iter_required(self):
        loop = self._loop()
        del loop["max_iter"]
        with pytest.raises(ValidationError, match="max_iter"):
            WorkflowDSL.model_validate({**_minimal_doc(), "steps": [loop]})

    def test_max_iter_bounds(self):
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate({**_minimal_doc(), "steps": [self._loop(max_iter=0)]})
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate({**_minimal_doc(), "steps": [self._loop(max_iter=99)]})

    def test_body_min_one(self):
        loop = self._loop()
        loop["body"] = []
        with pytest.raises(ValidationError, match="body"):
            WorkflowDSL.model_validate({**_minimal_doc(), "steps": [loop]})

    def test_until_tool_requires_tool_and_check(self):
        loop = self._loop(until={"type": "tool"})
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate({**_minimal_doc(), "steps": [loop]})

    def test_until_tool_valid(self):
        loop = self._loop(until={"type": "tool", "tool": "test_runner", "check": "tests_all_pass"})
        doc = WorkflowDSL.model_validate({**_minimal_doc(), "steps": [loop]})
        assert doc.steps[0].until.type == "tool"

    def test_until_agent_requires_question_expect_fallback(self):
        loop = self._loop(until={"type": "agent", "question": "¿aprobado?", "expect": "APROBADO"})
        doc = WorkflowDSL.model_validate({**_minimal_doc(), "steps": [loop]})
        assert doc.steps[0].until.fallback == "fail"

    def test_until_agent_bad_fallback_rejected(self):
        loop = self._loop(
            until={
                "type": "agent",
                "question": "¿aprobado?",
                "expect": "APROBADO",
                "fallback": "reintenta",
            }
        )
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate({**_minimal_doc(), "steps": [loop]})

    def test_until_metric_requires_metric_op_value(self):
        loop = self._loop(until={"type": "metric", "tool": "eval_suite", "metric": "pass_rate"})
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate({**_minimal_doc(), "steps": [loop]})
        ok = self._loop(
            until={
                "type": "metric",
                "tool": "eval_suite",
                "metric": "pass_rate",
                "op": ">=",
                "value": 0.9,
            }
        )
        doc = WorkflowDSL.model_validate({**_minimal_doc(), "steps": [ok]})
        assert doc.steps[0].until.op == ">="

    def test_until_unknown_type_rejected(self):
        loop = self._loop(until={"type": "vibes"})
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate({**_minimal_doc(), "steps": [loop]})

    def test_over_var_identifier(self):
        loop = self._loop(over="historias")
        doc = WorkflowDSL.model_validate({**_minimal_doc(), "steps": [loop]})
        assert doc.steps[0].over == "historias"


class TestDecideStep:
    def _decide(self, **over) -> dict:
        base = {
            "id": "elegir",
            "kind": "decide",
            "question": "¿qué camino?",
            "options": ["camino_a", "camino_b"],
            "fallback": "camino_a",
            "branches": {
                "camino_a": [{"id": "a1", "kind": "agent", "agent": "developer"}],
                "camino_b": [{"id": "b1", "kind": "agent", "agent": "analista"}],
            },
        }
        base.update(over)
        return base

    def test_valid_decide(self):
        doc = WorkflowDSL.model_validate({**_minimal_doc(), "steps": [self._decide()]})
        assert set(doc.steps[0].branches.keys()) == {"camino_a", "camino_b"}

    def test_options_min_two(self):
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate(
                {**_minimal_doc(), "steps": [self._decide(options=["solo"])]}
            )

    def test_fallback_must_be_option(self):
        with pytest.raises(ValidationError, match="fallback"):
            WorkflowDSL.model_validate(
                {**_minimal_doc(), "steps": [self._decide(fallback="camino_z")]}
            )

    def test_branches_must_cover_exactly_options(self):
        decide = self._decide()
        del decide["branches"]["camino_b"]
        with pytest.raises(ValidationError, match="camino_b"):
            WorkflowDSL.model_validate({**_minimal_doc(), "steps": [decide]})
        decide2 = self._decide()
        decide2["branches"]["camino_extra"] = []
        with pytest.raises(ValidationError, match="camino_extra"):
            WorkflowDSL.model_validate({**_minimal_doc(), "steps": [decide2]})

    def test_duplicate_options_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate(
                {
                    **_minimal_doc(),
                    "steps": [self._decide(options=["a", "a"], fallback="a")],
                }
            )


class TestOtrosSteps:
    def test_parallel_valid(self):
        raw = _minimal_doc()
        raw["steps"] = [
            {
                "id": "par",
                "kind": "parallel",
                "branches": [
                    {"steps": [{"id": "x1", "kind": "agent", "agent": "developer"}]},
                    {
                        "steps": [{"id": "x2", "kind": "agent", "agent": "analista"}],
                        "depends_on": ["x1"],
                    },
                ],
            }
        ]
        doc = WorkflowDSL.model_validate(raw)
        assert doc.steps[0].branches[1].depends_on == ["x1"]

    def test_parallel_empty_branches_rejected(self):
        raw = _minimal_doc()
        raw["steps"] = [{"id": "par", "kind": "parallel", "branches": []}]
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate(raw)

    def test_workflow_include_valid(self):
        raw = _minimal_doc()
        raw["steps"] = [
            {
                "id": "sub",
                "kind": "workflow",
                "name": "bdd",
                "inputs": {"historias_from": "$spec"},
                "outputs": ["codigo"],
            }
        ]
        doc = WorkflowDSL.model_validate(raw)
        assert doc.steps[0].name == "bdd"

    def test_checkpoint_valid(self):
        raw = _minimal_doc()
        raw["steps"].append({"id": "human", "kind": "checkpoint", "question": "¿Seguimos?"})
        WorkflowDSL.model_validate(raw)

    def test_tool_step_outputs_mapping(self):
        raw = _minimal_doc()
        raw["steps"] = [
            {
                "id": "tests",
                "kind": "tool",
                "tool": "test_runner",
                "outputs": {"counts": "counts"},
            }
        ]
        doc = WorkflowDSL.model_validate(raw)
        assert doc.steps[0].outputs == {"counts": "counts"}

    def test_aggregate_strategies(self):
        raw = _minimal_doc()
        raw["steps"].append(
            {"id": "agg", "kind": "aggregate", "strategy": "moderator", "agent": "moderador"}
        )
        doc = WorkflowDSL.model_validate(raw)
        assert doc.steps[1].strategy == "moderator"

    def test_aggregate_bad_strategy_rejected(self):
        raw = _minimal_doc()
        raw["steps"].append({"id": "agg", "kind": "aggregate", "strategy": "votacion_popular"})
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate(raw)

    def test_gate_custom_pattern_compiles(self):
        raw = _minimal_doc()
        raw["steps"][0]["gate"] = "\\[CRITICO\\]"
        doc = WorkflowDSL.model_validate(raw)
        assert doc.steps[0].gate == "\\[CRITICO\\]"

    def test_gate_bad_regex_rejected(self):
        raw = _minimal_doc()
        raw["steps"][0]["gate"] = "([unclosed"
        with pytest.raises(ValidationError, match="gate"):
            WorkflowDSL.model_validate(raw)

    def test_on_error_literal(self):
        raw = _minimal_doc()
        raw["steps"][0]["on_error"] = "continue"
        doc = WorkflowDSL.model_validate(raw)
        assert doc.steps[0].on_error == "continue"
        raw["steps"][0]["on_error"] = "ignorar"
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate(raw)


class TestConfigBasica:
    def test_deny_by_default_tools(self):
        raw = _minimal_doc()
        doc = WorkflowDSL.model_validate(raw)
        assert doc.tools.allowed is None  # → policy la traduce a []

    def test_agents_tools_project_reusados(self):
        raw = _minimal_doc()
        raw["agents"] = {"allowed": ["developer"]}
        raw["tools"] = {"allowed": ["file_manager"]}
        raw["project"] = {"required": True}
        raw["skills"] = True
        raw["commit_after"] = ["verify"]
        doc = WorkflowDSL.model_validate(raw)
        assert doc.agents.allowed == ["developer"]
        assert doc.tools.allowed == ["file_manager"]
        assert doc.project.required is True
        assert doc.skills is True
        assert doc.commit_after == ["verify"]

    def test_unknown_agents_field_rejected(self):
        raw = _minimal_doc()
        raw["agents"] = {"allowed": ["developer"], "extra": 1}
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate(raw)


class TestUntilArgs:
    """Until tool/metric debe poder declarar args — sin esto
    solo puede invocar tools zero-arg y test_runner (file_path requerido) muere
    en TypeError → los loops siempre agotan max_iter."""

    def _loop_doc(self, until: dict) -> dict:
        doc = _minimal_doc()
        doc["agents"] = {"allowed": ["developer"]}
        doc["tools"] = {"allowed": ["test_runner"]}
        doc["steps"] = [
            {
                "id": "ciclo",
                "kind": "loop",
                "max_iter": 3,
                "until": until,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "g"}],
            }
        ]
        return doc

    def test_until_tool_acepta_args(self):
        doc = self._loop_doc(
            {
                "type": "tool",
                "tool": "test_runner",
                "check": "tests_all_pass",
                "args": {"file_path": "."},
            }
        )
        dsl = WorkflowDSL.model_validate(doc)
        loop = dsl.steps[0]
        assert loop.until.args == {"file_path": "."}

    def test_until_metric_acepta_args(self):
        doc = self._loop_doc(
            {
                "type": "metric",
                "tool": "test_runner",
                "metric": "passed_count",
                "op": ">=",
                "value": 5,
                "args": {"file_path": "."},
            }
        )
        dsl = WorkflowDSL.model_validate(doc)
        assert dsl.steps[0].until.args == {"file_path": "."}

    def test_until_rechaza_campos_desconocidos(self):
        doc = self._loop_doc(
            {
                "type": "tool",
                "tool": "test_runner",
                "check": "tests_all_pass",
                "args": {"file_path": "."},
                "args2": {"x": 1},
            }
        )
        with pytest.raises(ValidationError):
            WorkflowDSL.model_validate(doc)

    def test_until_sin_args_default_vacio(self):
        doc = self._loop_doc({"type": "tool", "tool": "test_runner", "check": "tests_all_pass"})
        dsl = WorkflowDSL.model_validate(doc)
        assert dsl.steps[0].until.args == {}
