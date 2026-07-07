# tests/test_desktop_events.py
"""Tests para la capa de eventos desktop (señales Qt)."""

import pytest

from orchestration.context import WorkflowEvents

pytest.importorskip("PySide6")


def test_build_workflow_events_returns_workflow_events():
    """Verifica que build_workflow_events() retorna un WorkflowEvents."""
    from desktop.events import build_workflow_events

    events = build_workflow_events()
    assert isinstance(events, WorkflowEvents)
    # Los callbacks de desktop emiten señales Qt — verificamos que existen
    assert events.on_system_message is not None
    assert events.on_assistant_message is not None
    assert events.on_agent_message is not None
    assert events.on_stream_chunk is not None
    assert events.on_stats_update is not None
    assert events.on_diagram_update is not None
