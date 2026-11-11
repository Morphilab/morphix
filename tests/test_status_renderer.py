# tests/test_status_renderer.py
"""Tests para el renderizador HTML de estado del workflow."""

from orchestration.status import DEFAULT_CARD_COLORS, _clean_text, render_from_subtasks


def test_clean_text_escapes_html():
    result = _clean_text('<script>alert("xss")</script>')
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert "&quot;" in result


def test_clean_text_truncates_long():
    result = _clean_text("a" * 300)
    assert len(result) < 300
    assert result.endswith("...")


def test_renders_cards_grouped_by_phase():
    html = render_from_subtasks(
        [
            {"name": "T1", "status": "completed", "agent": "developer"},
            {"name": "T2", "status": "running", "agent": "developer"},
        ],
        phase="Implementación",
    )
    assert "Implementación" in html
    assert "COMPLETED" in html
    assert "RUNNING" in html
    assert "T1" in html
    assert "T2" in html


def test_empty_list_returns_placeholder():
    result = render_from_subtasks([])
    assert "Workflow vacío" in result


def test_escapes_html_in_task_names():
    html = render_from_subtasks([{"name": "<script>bad()</script>", "status": "done"}])
    assert "<script>" not in html
    assert "&lt;script&gt;bad()&lt;/script&gt;" in html


def test_render_from_subtasks_accepts_injected_colors():
    html = render_from_subtasks(
        [{"name": "T", "status": "completed"}], colors={"completed": "#ABCDEF"}
    )
    assert "#ABCDEF" in html


def test_render_defaults_match_current_palette():
    html = render_from_subtasks([{"name": "X", "status": "pending"}])
    assert DEFAULT_CARD_COLORS["card_bg"] in html
    assert DEFAULT_CARD_COLORS["body_bg"] in html
