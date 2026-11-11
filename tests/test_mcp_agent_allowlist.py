# tests/test_mcp_agent_allowlist.py
"""El wildcard `mcp` hace alcanzables las tools MCP desde los presets."""

from pathlib import Path

import pytest
import yaml

from tools.specs import TOOL_DEFINITIONS, expand_allowed_tools, tool_matches_allowlist

_ROOT = Path(__file__).resolve().parent.parent


def test_expand_allowed_tools_expande_wildcard_mcp(monkeypatch):
    monkeypatch.setitem(TOOL_DEFINITIONS, "mcp_demo_tool", TOOL_DEFINITIONS["file_manager"])
    assert "mcp_demo_tool" in expand_allowed_tools(["mcp"])


def test_tool_matches_allowlist_acepta_sanitizado_y_colon():
    assert tool_matches_allowlist("mcp_demo_tool", ["mcp"])
    assert tool_matches_allowlist("mcp:demo.tool", ["mcp"])


def test_development_y_coordinated_declaran_mcp():
    for name in ("development", "coordinated"):
        doc = yaml.safe_load(
            (_ROOT / "templates" / "workflows" / f"{name}.yaml").read_text(encoding="utf-8")
        )
        assert "mcp" in doc["tools"]["allowed"], f"{name} no declara el wildcard mcp"


@pytest.mark.asyncio
async def test_execute_tool_call_pasa_workspace_al_wrapper(monkeypatch):
    """El gate de trust solo corre con workspace EXPLÍCITO en safe_tool_call."""
    from orchestration.loop import _execute_single_tool_call

    captured = {}

    async def fake_safe(tool_name, parameters, **kw):
        captured.update(kw)
        return {"success": True, "output": "ok"}

    monkeypatch.setattr("orchestration.loop.safe_tool_call", fake_safe)
    await _execute_single_tool_call(
        "file_manager", {"action": "read", "path": "x"}, project_root=None, workspace="main"
    )
    assert captured["workspace"] == "main"


def test_preset_sin_wildcard_no_ve_mcp(monkeypatch):
    monkeypatch.setitem(TOOL_DEFINITIONS, "mcp_demo_tool", TOOL_DEFINITIONS["file_manager"])
    assert "mcp_demo_tool" not in expand_allowed_tools(["file_manager"])
