# tests/test_diagram_manager.py
"""Tests para el gestor de diagramas en vivo."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestration.diagram import update_live_diagram


@pytest.mark.asyncio
async def test_update_live_diagram_returns_none_when_g_is_none():
    events = MagicMock()
    result = await update_live_diagram(None, events)
    assert result is None


@pytest.mark.asyncio
async def test_update_live_diagram_returns_html_with_valid_graph():
    g = MagicMock()
    g.number_of_nodes.return_value = 1
    g.nodes.return_value = [0]
    g.nodes.__getitem__.side_effect = lambda n: {
        0: {"task": "Test", "agent": "developer", "status": "completed"},
    }[n]

    events = MagicMock()
    events.on_diagram_update = AsyncMock()
    events.on_ui_refresh = AsyncMock()

    result = await update_live_diagram(g, events)
    assert result is not None
    assert "COMPLETED" in result
    events.on_diagram_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_live_diagram_handles_none_events():
    g = MagicMock()
    g.number_of_nodes.return_value = 1
    result = await update_live_diagram(g, None)
    assert result is None
