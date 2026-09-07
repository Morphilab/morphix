"""Tarea 4 doc skills: bootstrap de skills condicionado por workflow."""

from core.skills import SkillSummary, apply_skills_bootstrap


def test_disabled_returns_untouched():
    ctx, tools = apply_skills_bootstrap("CTX", ["file_manager"], enabled=False, workspace="w")
    assert ctx == "CTX"
    assert tools == ["file_manager"]


def test_enabled_injects_bootstrap_and_expands_allowlist(monkeypatch):
    monkeypatch.setattr(
        "core.skills.discover_skills",
        lambda ws: [SkillSummary("writing-plans", "planes", "global")],
    )
    ctx, tools = apply_skills_bootstrap("CTX", ["file_manager"], enabled=True, workspace="w")
    assert ctx.startswith("<SKILLS>")
    assert "- **writing-plans** (global): planes" in ctx
    assert tools == ["file_manager", "load_skill"]


def test_none_allowlist_becomes_list(monkeypatch):
    monkeypatch.setattr("core.skills.discover_skills", lambda ws: [])
    _ctx, tools = apply_skills_bootstrap("CTX", None, enabled=True, workspace=None)
    assert tools == ["load_skill"]


def test_no_duplicate_load_skill(monkeypatch):
    monkeypatch.setattr("core.skills.discover_skills", lambda ws: [])
    _ctx, tools = apply_skills_bootstrap("CTX", ["load_skill"], enabled=True, workspace=None)
    assert tools.count("load_skill") == 1


def test_loop_accepts_skills_enabled_kwarg():
    """El loop expone skills_enabled y la firma sigue compatible."""
    import inspect

    from orchestration.loop import execute_agent_loop

    sig = inspect.signature(execute_agent_loop)
    assert "skills_enabled" in sig.parameters
    assert sig.parameters["skills_enabled"].default is False
