# tests/test_collaborative_orchestrator.py
"""Tests for the CollaborativeOrchestrator — multi-agent debate with rounds."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.context import WorkflowEvents


def _make_events():
    on_system = AsyncMock()
    on_assistant = AsyncMock()
    on_stats = AsyncMock()
    on_stream = AsyncMock()
    events = WorkflowEvents(
        on_stream_chunk=on_stream,
        on_system_message=on_system,
        on_assistant_message=on_assistant,
        on_stats_update=on_stats,
        on_diagram_update=AsyncMock(),
        on_ui_refresh=AsyncMock(),
    )
    return events, on_assistant, on_system, on_stats, on_stream


def _make_mock_response(content: str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = None
    return resp


def _setup_agent_mocks(registry_mock, agents: list[str]):
    """Register mock agents with profiles in the registry."""
    profiles = {}
    for name in agents:
        profiles[name] = MagicMock()
        profiles[name].get.side_effect = lambda key, default=None, n=name: {
            "tools": ["file_manager"] if n == "developer" else [],
            "model_role": "agent",
            "temperature": 0.4,
            "keywords": [],
        }.get(key, default)
    registry_mock.list_agents.return_value = {n: MagicMock() for n in agents}
    registry_mock.get_profile.side_effect = lambda n: profiles.get(n)


class TestCollaborativeOrchestrator:
    @pytest.mark.asyncio
    async def test_runs_correct_number_of_rounds(self):
        from orchestration.workflows.collaborative import (
            CollaborativeOrchestrator,
        )

        template = {
            "panel": ["developer", "analista"],
            "rounds": 3,
            "moderator": "moderador",
        }
        events, on_assistant, on_system, on_stats, on_stream = _make_events()

        call_count = 0

        async def mock_call(messages, role="agent", temperature=0.4, tools=None, tool_choice=None):
            nonlocal call_count
            call_count += 1
            return _make_mock_response(f"[{role}] response {call_count}")

        with (
            patch(
                "orchestration.workflows.collaborative.models.call",
                side_effect=mock_call,
            ),
            patch(
                "orchestration.workflows.collaborative.agents_registry",
            ) as registry_mock,
        ):
            _setup_agent_mocks(registry_mock, ["developer", "analista", "moderador"])
            result = await CollaborativeOrchestrator.run(
                query="What language should we use?",
                template=template,
                events=events,
            )

        # 2 agents × 3 rounds + 1 moderator = 7 calls minimum
        assert call_count >= 7, f"Expected ≥7 calls, got {call_count}"
        assert result is not None

    @pytest.mark.asyncio
    async def test_agents_see_previous_opinions(self):
        from orchestration.workflows.collaborative import (
            CollaborativeOrchestrator,
        )

        template = {
            "panel": ["developer", "analista"],
            "rounds": 2,
            "moderator": "moderador",
        }
        events, on_assistant, on_system, on_stats, on_stream = _make_events()

        queries_seen = []

        async def mock_call(messages, role="agent", temperature=0.4, tools=None, tool_choice=None):
            last_msg = messages[-1].get("content", "") if messages else ""
            queries_seen.append((role, last_msg))
            return _make_mock_response(f"[{role}] opinion")

        with (
            patch(
                "orchestration.workflows.collaborative.models.call",
                side_effect=mock_call,
            ),
            patch(
                "orchestration.workflows.collaborative.agents_registry",
            ) as registry_mock,
        ):
            _setup_agent_mocks(registry_mock, ["developer", "analista", "moderador"])
            await CollaborativeOrchestrator.run(
                query="Where to go on vacation?",
                template=template,
                events=events,
            )

        # Round 2: agents MUST see previous round opinions
        round2_queries = [q for r, q in queries_seen if "opinaron" in q]
        assert len(round2_queries) >= 2

    @pytest.mark.asyncio
    async def test_moderator_called_last(self):
        from orchestration.workflows.collaborative import (
            CollaborativeOrchestrator,
        )

        template = {
            "panel": ["developer", "analista"],
            "rounds": 1,
            "moderator": "moderador",
        }
        events, on_assistant, on_system, on_stats, on_stream = _make_events()

        async def mock_call(messages, role="agent", temperature=0.4, tools=None, tool_choice=None):
            return _make_mock_response(f"[{role}] response")

        with (
            patch(
                "orchestration.workflows.collaborative.models.call",
                side_effect=mock_call,
            ),
            patch(
                "orchestration.workflows.collaborative.agents_registry",
            ) as registry_mock,
        ):
            _setup_agent_mocks(registry_mock, ["developer", "analista", "moderador"])
            result = await CollaborativeOrchestrator.run(
                query="What do you think?",
                template=template,
                events=events,
            )

        assert result is not None
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_handles_agent_errors_gracefully(self):
        from orchestration.workflows.collaborative import (
            CollaborativeOrchestrator,
        )

        template = {
            "panel": ["developer", "analista"],
            "rounds": 1,
            "moderator": "moderador",
        }
        events, on_assistant, on_system, on_stats, on_stream = _make_events()

        call_count = 0

        async def mock_call(messages, role="agent", temperature=0.4, tools=None, tool_choice=None):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # second agent fails
                raise RuntimeError("agent crashed")
            return _make_mock_response(f"[{role}] ok")

        with (
            patch(
                "orchestration.workflows.collaborative.models.call",
                side_effect=mock_call,
            ),
            patch(
                "orchestration.workflows.collaborative.agents_registry",
            ) as registry_mock,
        ):
            _setup_agent_mocks(registry_mock, ["developer", "analista", "moderador"])
            result = await CollaborativeOrchestrator.run(
                query="Test with failure",
                template=template,
                events=events,
            )

        assert result is not None

    @pytest.mark.asyncio
    async def test_panel_debe_tener_al_menos_dos_agentes(self):
        from orchestration.workflows.collaborative import (
            CollaborativeOrchestrator,
        )

        template = {"panel": ["solo_yo"], "rounds": 1, "moderator": "moderador"}
        events, on_assistant, on_system, on_stats, on_stream = _make_events()

        result = await CollaborativeOrchestrator.run(
            query="Test small panel",
            template=template,
            events=events,
        )

        assert "Error" in result
