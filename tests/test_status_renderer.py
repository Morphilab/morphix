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
