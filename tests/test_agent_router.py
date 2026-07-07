# tests/test_agent_router.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_router_llm():
    """Mock del LLM para AgentRouter."""
    with patch(
        "orchestration.router.models.call",
        new_callable=AsyncMock,
    ) as mock_call:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        yield mock_call, mock_response


def _make_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


@pytest.mark.asyncio
async def test_select_agent_developer(mock_router_llm):
    mock_call, _ = mock_router_llm
    mock_call.return_value = _make_response('{"agent": "developer"}')

    from orchestration.router import agent_router

    agent = await agent_router.select_best_agent(
        "Create a REST endpoint with FastAPI", primary_type="developer"
    )
    assert isinstance(agent, str)
    assert len(agent) > 0


@pytest.mark.asyncio
async def test_select_agent_with_allowed_filter(mock_router_llm):
    mock_call, _ = mock_router_llm
    mock_call.return_value = _make_response('{"agent": "developer"}')

    from agents.registry import agents_registry
    from orchestration.router import agent_router

    # Ensure test agents are in the registry so the filter works
    agents_registry.register_workspace_agent("developer", AsyncMock(), {"name": "developer"})
    agents_registry.register_workspace_agent("analista", AsyncMock(), {"name": "analista"})

    agent = await agent_router.select_best_agent(
        "Optimize SQL queries",
        primary_type="developer",
        allowed_agents=["developer", "analista"],
    )
    assert agent in ["developer", "analista"]


@pytest.mark.asyncio
async def test_select_agent_conversational(mock_router_llm):
    mock_call, _ = mock_router_llm
    mock_call.return_value = _make_response('{"agent": "conversacional"}')

    from orchestration.router import agent_router

    agent = await agent_router.select_best_agent("¿Cómo funciona Python?")
    assert isinstance(agent, str)


@pytest.mark.asyncio
async def test_select_agent_llm_error_fallback(mock_router_llm):
    mock_call, _ = mock_router_llm
    mock_call.side_effect = RuntimeError("LLM timeout")

    from orchestration.router import agent_router

    agent = await agent_router.select_best_agent("Cualquier tarea")
    assert isinstance(agent, str)
    assert len(agent) > 0
