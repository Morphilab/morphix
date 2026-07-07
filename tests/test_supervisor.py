# tests/test_supervisor.py
"""Tests para el supervisor de selección de agentes."""

from unittest.mock import MagicMock, patch

import pytest

from orchestration.supervisor import WorkflowSupervisor


@pytest.mark.asyncio
async def test_supervisor_corrects_with_keyword_match():
    registry = MagicMock()
    registry.list_agents.return_value = {"developer": MagicMock(), "analista": MagicMock()}
    registry.get_profile.side_effect = lambda name: (
        {"keywords": ["code", "python", "file", "implement"]}
        if name == "developer"
        else {"keywords": ["docs", "readme", "analyze"]}
    )

    with patch("orchestration.supervisor.agents_registry", registry):
        result = await WorkflowSupervisor.review_and_correct(
            task_analyzer_result={},
            router_selections=["developer", "developer"],
            subtasks=["Create python file", "Write README for project"],
        )
        assert result[0] == "developer"
        assert result[1] == "analista"


@pytest.mark.asyncio
async def test_supervisor_falls_back_when_no_keyword_match():
    registry = MagicMock()
    registry.list_agents.return_value = {"developer": None, "analista": None}
    registry.get_profile.side_effect = lambda name: {
        "developer": {"keywords": ["code"]},
        "analista": {"keywords": ["data"]},
    }.get(name, {})

    with patch("orchestration.supervisor.agents_registry", registry):
        result = await WorkflowSupervisor.review_and_correct(
            task_analyzer_result={},
            router_selections=["developer", "developer"],
            subtasks=["Do something without keywords", "Another thing"],
        )
        # No keyword match → keeps router selection
        assert result[0] == "developer"
        assert result[1] == "developer"


@pytest.mark.asyncio
async def test_supervisor_respects_allowed_agents_filter():
    registry = MagicMock()
    registry.list_agents.return_value = {"developer": None, "analista": None, "moderador": None}
    registry.get_profile.side_effect = lambda name: {
        "developer": {"keywords": ["code"]},
        "analista": {"keywords": ["docs", "README"]},
        "moderador": {"keywords": ["debate"]},
    }.get(name, {})

    with patch("orchestration.supervisor.agents_registry", registry):
        result = await WorkflowSupervisor.review_and_correct(
            task_analyzer_result={},
            router_selections=["developer", "developer"],
            subtasks=["Create code", "Write README"],
            allowed_agents=["developer"],  # only developer allowed
        )
        # Both tasks → developer (only allowed)
        assert result[0] == "developer"
        assert result[1] == "developer"


@pytest.mark.asyncio
async def test_supervisor_returns_router_selections_when_no_allowed():
    registry = MagicMock()
    registry.list_agents.return_value = {}
    registry.get_profile.return_value = {}

    with patch("orchestration.supervisor.agents_registry", registry):
        result = await WorkflowSupervisor.review_and_correct(
            task_analyzer_result={},
            router_selections=["developer", "analista"],
            subtasks=["a", "b"],
            allowed_agents=[],  # empty list → none allowed
        )
        assert result == ["developer", "analista"]
