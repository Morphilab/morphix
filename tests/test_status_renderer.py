# tests/test_status_renderer.py
"""Tests para el renderizador HTML de estado del workflow."""

from unittest.mock import MagicMock

from orchestration.status import _clean_text, render


def test_render_empty_graph_returns_placeholder():
    result = render(None)
    assert "Workflow vacío" in result

    g = MagicMock()
    g.number_of_nodes.return_value = 0
    result = render(g)
    assert "Workflow vacío" in result


def test_render_graph_with_nodes():
    g = MagicMock()
    g.number_of_nodes.return_value = 2
    g.nodes.return_value = [0, 1]
    g.nodes.__getitem__.side_effect = lambda n: {
        0: {"task": "Crear app.py", "agent": "developer", "status": "completed"},
        1: {"task": "Escribir tests", "agent": "developer", "status": "running"},
    }[n]

    result = render(g)
    assert "COMPLETED" in result
    assert "RUNNING" in result
    assert "Crear app.py" in result
    assert "Escribir tests" in result
    assert "developer" in result


def test_clean_text_escapes_html():
    result = _clean_text('<script>alert("xss")</script>')
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert "&quot;" in result


def test_clean_text_truncates_long():
    long_text = "a" * 150
    result = _clean_text(long_text, max_len=100)
    assert len(result) <= 103
    assert result.endswith("...")


def test_renders_cards_grouped_by_phase():
    from orchestration.status import render_from_subtasks

    subtask_list = [
        {"name": "Diseñar", "status": "completed"},
        {"name": "Implementar", "status": "running"},
        {"name": "Verificar", "status": "pending"},
    ]
    html = render_from_subtasks(subtask_list, phase="implement")

    assert "Diseñar" in html
    assert "Implementar" in html
    assert "Verificar" in html
    assert "COMPLETED" in html.upper()
    assert "RUNNING" in html.upper()
    assert "implement" in html.lower()


def test_empty_list_returns_placeholder():
    from orchestration.status import render_from_subtasks

    html = render_from_subtasks([], phase=None)
    assert "Workflow vacío" in html


def test_escapes_html_in_task_names():
    from orchestration.status import render_from_subtasks

    html = render_from_subtasks([{"name": "<script>alert(1)</script>", "status": "pending"}])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
