# tests/test_skills_wiring.py
"""Skills procedurales: chat directo y bots, no solo presets DSL."""

from unittest.mock import AsyncMock, patch

import pytest
from _agent_loop_support import mock_agent_deps  # noqa: E402,F401 — fixture compartida


@pytest.mark.asyncio
async def test_run_direct_agent_pasa_skills_enabled():
    from desktop.services.workflow_runner import WorkflowRunner

    runner = WorkflowRunner()
    runner.on_assistant = AsyncMock()
    session = AsyncMock()
    session.events = None
    session.context.conversation_history = []
    session.context.project_root = None

    with (
        patch(
            "agents.registry.agents_registry.get_profile",
            return_value={"tools": ["file_manager"]},
        ),
        patch("tools.specs.expand_allowed_tools", return_value=["file_manager"]),
        patch("orchestration.loader.load_workflow_document", return_value=None),
        patch("orchestration.loop.execute_agent_loop", new_callable=AsyncMock) as mock_loop,
    ):
        mock_loop.return_value = {"status": "done", "result": "ok", "files_written": []}
        await runner.run_direct_agent(session, "haz x", "developer")

    assert mock_loop.call_args.kwargs["skills_enabled"] is True


def test_apply_skills_bootstrap_filtra_por_allowlist(monkeypatch):
    from core.skills import SkillSummary, apply_skills_bootstrap

    monkeypatch.setattr(
        "core.skills.discover_skills",
        lambda ws: [
            SkillSummary("writing-plans", "planes", "global"),
            SkillSummary("brainstorming", "ideas", "global"),
        ],
    )
    ctx, tools = apply_skills_bootstrap(
        "CTX", ["file_manager"], enabled=True, workspace="w", allowlist=["writing-plans"]
    )
    assert "- **writing-plans**" in ctx
    assert "- **brainstorming**" not in ctx
    assert tools == ["file_manager", "load_skill"]


@pytest.mark.asyncio
async def test_bot_conserva_load_skill_en_el_allowlist(mock_agent_deps):
    from orchestration.loop import execute_agent_loop

    captured: dict = {}

    def fake_btd(allowed, **kw):
        captured["allowed"] = allowed
        return []

    with (
        patch("orchestration.loop.build_tool_definitions", side_effect=fake_btd),
        patch("core.bots_epoch.refresh_if_stale", new_callable=AsyncMock),
        patch("orchestration.loop._bot_memory_snapshot", return_value=""),
        patch("orchestration.loop._bot_skills_bootstrap", return_value=""),
    ):
        await execute_agent_loop(
            task="hola",
            agent_type="conversacional",
            workspace="main",
            allowed_tools=["file_manager"],
            skills_enabled=True,
            bot_context={
                "slug": "demo",
                "tool_names": ["file_manager"],
                "dm_enabled": False,
                "skill_allowlist": ["writing-plans"],
                "workspace": "main",
                "conversation_id": 1,
            },
        )

    assert "load_skill" in captured["allowed"]
    from orchestration.context import skill_allowlist_active

    assert skill_allowlist_active.get() == ["writing-plans"]


@pytest.mark.asyncio
async def test_load_skill_rechaza_fuera_del_allowlist(monkeypatch):
    from types import SimpleNamespace

    from orchestration.context import skill_allowlist_active
    from tools.skill_loader import _load_skill_tool

    monkeypatch.setattr(
        "tools.skill_loader.load_skill",
        lambda name, ws: SimpleNamespace(name=name, body="body", origin="global"),
    )
    skill_allowlist_active.set(["writing-plans"])
    try:
        out = await _load_skill_tool(name="brainstorming")
    finally:
        skill_allowlist_active.set(None)
    assert "allowlist" in out.lower()
