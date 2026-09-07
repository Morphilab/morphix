"""Tests de los helpers de las cards de workflows del Dashboard."""

from desktop.dashboard_tab import (
    _display_workflow_name,
    _truncate_text,
    _workflow_meta,
)


def test_display_name_title_case_acronyms():
    assert _display_workflow_name("domain_tdd") == "Domain TDD"
    assert _display_workflow_name("bdd") == "BDD"
    assert _display_workflow_name("sdd") == "SDD"
    assert _display_workflow_name("edd") == "EDD"
    assert _display_workflow_name("coordinated") == "Coordinated"


def test_truncate_short_text_untouched():
    assert _truncate_text("hola mundo", 70) == "hola mundo"


def test_truncate_cuts_at_word_boundary_with_ellipsis():
    desc = "Multi-agent coordinator — decompose, execute per subtask, aggregate"
    result = _truncate_text(desc, 40)
    assert result.endswith("…")
    assert len(result) <= 41
    # La última palabra completa no quedó cortada a mitad
    last_word = result[:-1].split(" ")[-1]
    assert last_word in desc


def test_truncate_normalizes_whitespace():
    assert _truncate_text("a   b\n c", 70) == "a b c"


def test_workflow_meta_empty_view():
    assert _workflow_meta({}) == ""


def test_workflow_meta_full():
    meta = _workflow_meta(
        {
            "agents_allowed": ["a", "b"],
            "tools_allowed": ["t1", "t2", "t3"],
            "project_required": True,
        }
    )
    assert meta == "2 agentes · 3 tools · requiere proyecto"


def test_workflow_meta_without_project():
    meta = _workflow_meta({"agents_allowed": ["a"], "tools_allowed": []})
    assert meta == "1 agente"
