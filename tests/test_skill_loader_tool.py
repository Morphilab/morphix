# tests/test_skill_loader_tool.py
"""Tarea 3 doc skills: tool load_skill registrable y con mensajes útiles."""

from unittest.mock import patch

import pytest

from core import skills as sk
from tools.registry import ToolsRegistry
from tools.skill_loader import register as register_load_skill


@pytest.fixture
def registry():
    reg = ToolsRegistry()
    register_load_skill(reg)
    return reg


@pytest.mark.asyncio
async def test_load_skill_returns_body(registry, tmp_path):
    skill_dir = tmp_path / "brainstorming"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: brainstorming\ndescription: "d"\n---\n\nCUERPO-MARCADOR\n',
        encoding="utf-8",
    )
    with (
        patch("tools.skill_loader.discover_skills", return_value=[]),
        patch("core.workspaces.get_global_workspaces") as gw,
        patch("tools.skill_loader.load_skill") as ls,
    ):
        gw.return_value.current = "ws1"
        ls.return_value = sk.Skill(
            name="brainstorming",
            description="d",
            body="CUERPO-MARCADOR",
            path=skill_dir / "SKILL.md",
        )
        out = await registry.get_tool("load_skill")(name="brainstorming")
    assert "CUERPO-MARCADOR" in out


@pytest.mark.asyncio
async def test_load_skill_unknown_lists_available(registry):
    with (
        patch("tools.skill_loader.load_skill", side_effect=sk.SkillNotFoundError("zzz")),
        patch(
            "tools.skill_loader.discover_skills",
            return_value=[sk.SkillSummary("a", "d", "global")],
        ),
        patch("core.workspaces.get_global_workspaces") as gw,
    ):
        gw.return_value.current = "ws1"
        out = await registry.get_tool("load_skill")(name="zzz")
    assert "no existe" in out
    assert "Disponibles: a" in out


@pytest.mark.asyncio
async def test_load_skill_requires_name(registry):
    out = await registry.get_tool("load_skill")(name="")
    assert "requiere" in out


def test_spec_registered_and_flat():
    from tools.specs import TOOL_DEFINITIONS

    spec = TOOL_DEFINITIONS["load_skill"]
    assert set(spec.parameters.keys()) == {"name"}
    assert spec.required == ["name"]
