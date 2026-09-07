# tests/test_workflow_view.py
"""Tests de la vista unificada de workflows para la GUI (legacy + DSL).

Función pura (sin Qt): load_workflow_view normaliza ambos formatos al
mismo contrato; summarize_dsl cuenta kinds recursivamente (incluye
branches de parallel y decide).
"""

from desktop.services.workflow_view import is_dsl, load_workflow_view, summarize_dsl


def _dsl_doc() -> dict:
    return {
        "version": 1,
        "name": "demo",
        "description": "Workflow de prueba",
        "agents": {"allowed": ["developer"]},
        "tools": {"allowed": ["file_manager", "test_runner"]},
        "project": {"required": True},
        "skills": True,
        "steps": [
            {"id": "h", "kind": "agent", "agent": "developer"},
            {
                "id": "ciclo",
                "kind": "loop",
                "max_iter": 3,
                "body": [{"id": "d", "kind": "agent", "agent": "developer"}],
            },
            {
                "id": "par",
                "kind": "parallel",
                "branches": [{"steps": [{"id": "x", "kind": "tool", "tool": "test_runner"}]}],
            },
        ],
    }


def _legacy_doc() -> dict:
    return {
        "name": "legacy",
        "type": "collaborative",
        "description": "Viejo formato",
        "agents": {"allowed": ["analista"]},
        "tools": {"allowed": ["file_manager"]},
        "project": {"required": False},
        "skills": False,
        "panel": ["a", "b"],
    }


class TestIsDsl:
    def test_detecta_dsl(self):
        assert is_dsl(_dsl_doc()) is True
        assert is_dsl(_legacy_doc()) is False
        assert is_dsl(None) is False


class TestSummarizeDsl:
    def test_cuenta_kinds_recursivo(self):
        s = summarize_dsl(_dsl_doc())
        # agent ×3 (raíz + body del loop + ninguna), tool ×1, loop ×1, parallel ×1
        assert s["step_kinds"]["agent"] == 2
        assert s["step_kinds"]["tool"] == 1
        assert s["step_kinds"]["loop"] == 1
        assert s["step_kinds"]["parallel"] == 1

    def test_cuenta_decide_branches(self):
        doc = _dsl_doc()
        doc["steps"].append(
            {
                "id": "el",
                "kind": "decide",
                "options": ["a", "b"],
                "fallback": "a",
                "branches": {
                    "a": [{"id": "ea", "kind": "agent", "agent": "developer"}],
                    "b": [{"id": "eb", "kind": "agent", "agent": "developer"}],
                },
            }
        )
        s = summarize_dsl(doc)
        assert s["step_kinds"]["agent"] == 4
        assert s["step_kinds"]["decide"] == 1

    def test_campos_base(self):
        s = summarize_dsl(_dsl_doc())
        assert s["description"] == "Workflow de prueba"
        assert s["project_required"] is True
        assert s["skills"] is True
        assert s["dsl"] is True


class TestLoadWorkflowView:
    def test_dsl_desde_disco_real(self):
        # development.yaml es un preset DSL real del repo
        view = load_workflow_view(None, "development")
        assert view is not None
        assert view["dsl"] is True
        assert "developer" in view["agents_allowed"]
        # development escribe código → exige proyecto
        assert view["project_required"] is True
        assert "file_manager" in view["tools_allowed"]
        assert view["kind_summary"] is not None

    def test_inexistente_retorna_none(self):
        assert load_workflow_view(None, "no_existe_xyz") is None

    def test_legacy_doc_a_traves_de_document(self):
        # _FULL_TEMPLATE es legacy (sin version) pero empieza con _ → no
        # listado; usamos un doc legacy sintético vía monkeypatch del loader
        import orchestration.loader as loader_mod

        original = loader_mod.load_workflow_document

        def _fake(ws, name):
            return _legacy_doc() if name == "legacy_ws" else original(ws, name)

        loader_mod.load_workflow_document = _fake
        try:
            view = load_workflow_view(None, "legacy_ws")
        finally:
            loader_mod.load_workflow_document = original
        assert view is not None
        assert view["dsl"] is False
        assert view["agents_allowed"] == ["analista"]
        assert view["project_required"] is False
        assert view["kind_summary"] is None

    def test_dsl_con_project_required_true(self):
        import orchestration.loader as loader_mod

        original = loader_mod.load_workflow_document

        def _fake(ws, name):
            return _dsl_doc() if name == "req_dsl" else original(ws, name)

        loader_mod.load_workflow_document = _fake
        try:
            view = load_workflow_view(None, "req_dsl")
        finally:
            loader_mod.load_workflow_document = original
        assert view["project_required"] is True
