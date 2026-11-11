# tests/test_dsl_fragments.py — includes de body: un step `kind: fragment`
# se expande in-place en compile-time; el motor solo ve steps reales.
import pytest

from orchestration.dsl.compiler import compile_workflow
from orchestration.dsl.schema import WorkflowDSL


class FakeCatalog:
    def __init__(self, presets: dict):
        self.presets = presets

    def load(self, name):
        return self.presets.get(name)


def _agent(sid, goal="haz $query"):
    return {"id": sid, "kind": "agent", "agent": "developer", "goal": goal}


def _doc(steps, name="flujo"):
    return {
        "version": 1,
        "name": name,
        "description": "doc de test",
        "agents": {"allowed": ["developer"]},
        "tools": {"allowed": ["file_manager"]},
        "project": {"required": False},
        "steps": steps,
    }


def _frag(sid, name):
    return {"id": sid, "kind": "fragment", "include": name}


def _has_fragment(steps_raw) -> bool:
    return any(s.get("kind") == "fragment" for s in steps_raw)


class TestFragmentExpansion:
    def test_fragment_expands_in_place(self):
        cat = FakeCatalog(
            {
                "_dos": {"steps": [_agent("paso_a"), _agent("paso_b")]},
            }
        )
        cw = compile_workflow(
            _doc([_agent("inicio"), _frag("cuerpo", "_dos"), _agent("fin")]), catalog=cat
        )
        ids = [s.id for s in cw.dsl.steps]
        assert ids == ["inicio", "paso_a", "paso_b", "fin"]
        assert not any(s.kind == "fragment" for s in cw.dsl.steps)

    def test_fragment_inside_loop_body(self):
        cat = FakeCatalog({"_cuerpo": {"steps": [_agent("subtarea", "ejecuta $item")]}})
        doc = _doc(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "over": "items",
                    "body": [_frag("cuerpo", "_cuerpo")],
                }
            ]
        )
        cw = compile_workflow(doc, catalog=cat)
        loop = cw.dsl.steps[0]
        assert [s.id for s in loop.body] == ["subtarea"]

    def test_fragment_of_fragment_expands(self):
        cat = FakeCatalog(
            {
                "_ext": {"steps": [_agent("ext_paso")]},
                "_medio": {"steps": [_frag("hoja", "_ext"), _agent("medio_paso")]},
            }
        )
        cw = compile_workflow(_doc([_frag("cuerpo", "_medio")]), catalog=cat)
        assert [s.id for s in cw.dsl.steps] == ["ext_paso", "medio_paso"]

    def test_fragment_bare_list_root_supported(self):
        cat = FakeCatalog({"_lista": [_agent("suelto")]})
        cw = compile_workflow(_doc([_frag("cuerpo", "_lista")]), catalog=cat)
        assert [s.id for s in cw.dsl.steps] == ["suelto"]

    def test_fragment_missing_fail_loud(self):
        cat = FakeCatalog({})
        with pytest.raises(ValueError, match="No existe el fragmento"):
            compile_workflow(_doc([_frag("cuerpo", "_fantasma")]), catalog=cat)

    def test_fragment_cycle_fail_loud(self):
        cat = FakeCatalog({"_a": {"steps": [_frag("inner", "_a")]}})
        with pytest.raises(ValueError, match="iclo"):
            compile_workflow(_doc([_frag("cuerpo", "_a")]), catalog=cat)

    def test_fragment_without_catalog_fail_loud(self):
        with pytest.raises(ValueError, match="catálogo"):
            compile_workflow(_doc([_frag("cuerpo", "_algo")]), catalog=None)

    def test_fragment_id_collision_fail_loud(self):
        cat = FakeCatalog({"_choca": {"steps": [_agent("inicio")]}})
        with pytest.raises(Exception, match="duplic|nico"):
            compile_workflow(_doc([_agent("inicio"), _frag("cuerpo", "_choca")]), catalog=cat)

    def test_fragment_steps_validated_by_schema(self):
        from pydantic import ValidationError

        cat = FakeCatalog({"_malo": {"steps": [{"id": "x", "kind": "agent"}]}})
        with pytest.raises(ValidationError):
            compile_workflow(_doc([_frag("cuerpo", "_malo")]), catalog=cat)


def test_workflow_dsl_accepts_fragment_step_in_union():
    doc = _doc([_frag("cuerpo", "_algo")])
    dsl = WorkflowDSL.model_validate(doc)
    assert dsl.steps[0].kind == "fragment"
