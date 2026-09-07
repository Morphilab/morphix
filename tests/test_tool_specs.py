# tests/test_tool_specs.py
from unittest.mock import MagicMock

import pytest

from tools.specs import (
    TOOL_DEFINITIONS,
    ToolDefinition,
    build_tool_definitions,
    build_tool_instructions,
    expand_allowed_tools,
    tool_matches_allowlist,
)


def test_tool_definition_to_openai_spec():
    td = ToolDefinition(
        name="test_tool",
        description="Una herramienta de prueba",
        parameters={"action": {"type": "string", "description": "Acción a realizar"}},
    )
    spec = td.to_openai_spec()

    assert spec["type"] == "function"
    assert spec["function"]["name"] == "test_tool"
    assert "action" in spec["function"]["parameters"]["properties"]
    assert "action" in spec["function"]["parameters"]["required"]


def test_all_tools_have_definitions():
    """Las herramientas tienen ToolDefinition (14 core + 8 goal/todo/plan-mode)."""
    assert len(TOOL_DEFINITIONS) == 24  # +file_view
    expected = {
        "file_manager",
        "git_manager",
        "code_exec",
        "lsp_manager",
        "pdf_read",
        "test_runner",
        "diff_editor",
        "bash_manager",
        "web_search",
        "web_fetch",
        "code_search",
        "memory_inspector",
        "load_skill",
        "vision_analyze",
        "project_docs",
        # Absorción deepseek: goal/todo/plan-mode con CAS
        "goal_create",
        "goal_get",
        "goal_update",
        "goal_round",
        "todo_write",
        "todo_get",
        "plan_mode",
        "exit_plan_mode",
        "file_view",
    }
    assert set(TOOL_DEFINITIONS.keys()) == expected


def test_build_tool_definitions_all():
    """build_tool_definitions sin filtro devuelve todas."""
    defs = build_tool_definitions()
    assert len(defs) == 24
    for d in defs:
        assert d["type"] == "function"


def test_build_tool_definitions_filtered():
    """build_tool_definitions con filtro devuelve solo las permitidas."""
    defs = build_tool_definitions(allowed_tools=["file_manager", "git_manager"])
    assert len(defs) == 2
    names = [d["function"]["name"] for d in defs]
    assert "file_manager" in names
    assert "git_manager" in names


def test_build_tool_definitions_empty_filter():
    """Lista vacía no devuelve herramientas."""
    defs = build_tool_definitions(allowed_tools=[])
    assert len(defs) == 0


def test_build_tool_instructions_with_tools():
    """Instrucciones textuales incluyen herramientas permitidas."""
    text = build_tool_instructions(
        allowed_tools=["file_manager", "git_manager"],
        project_root="miapp",
        plan_mode=True,
    )
    assert "file_manager" in text
    assert "git_manager" in text
    assert "miapp" in text
    assert "acciones" in text.lower()


def test_build_tool_instructions_no_tools():
    """Sin herramientas devuelve mensaje claro."""
    text = build_tool_instructions(allowed_tools=[])
    assert "No hay herramientas" in text


def test_build_tool_instructions_uses_active_workspace(monkeypatch):
    """El prompt debe informar la ruta del workspace ACTIVO, no 'main'."""

    ws_mock = MagicMock()
    ws_mock.current = "prueba9"
    monkeypatch.setattr("core.workspaces.workspaces_instance", ws_mock)

    text = build_tool_instructions(
        allowed_tools=["file_manager"],
        project_root="miapp",
        plan_mode=False,
    )
    assert "miapp" in text
    assert "memory/prueba9" in text or "prueba9" in text
    assert "memory/main" not in text.replace("memory/main_", "")


def test_build_tool_instructions_none_allowed():
    """allowed_tools=None devuelve mensaje claro."""
    text = build_tool_instructions(allowed_tools=None)
    assert "No hay herramientas" in text or len(text) > 0


def test_strict_mode_requires_all_properties():
    """DeepSeek strict mode: all properties must be in required array."""
    td = ToolDefinition(
        name="multi_param",
        description="Tool with optional params",
        parameters={
            "command": {"type": "string", "description": "Required command"},
            "cwd": {"type": "string", "description": "Optional cwd"},
            "timeout": {"type": "integer", "description": "Optional timeout"},
        },
        required=["command"],
    )
    spec = td.to_openai_spec(strict=True)
    required = spec["function"]["parameters"]["required"]
    properties = spec["function"]["parameters"]["properties"]
    # Strict mode: ALL properties must be in required (DeepSeek strict mode rule)
    assert set(required) == set(
        properties.keys()
    ), f"strict mode: required={required} != properties={list(properties.keys())}"
    assert spec["function"]["strict"] is True
    assert spec["function"]["parameters"]["additionalProperties"] is False


def test_non_strict_mode_uses_explicit_required():
    """Non-strict mode respects explicit required field."""
    td = ToolDefinition(
        name="multi_param",
        description="Tool with optional params",
        parameters={
            "command": {"type": "string", "description": "Required command"},
            "cwd": {"type": "string", "description": "Optional cwd"},
            "timeout": {"type": "integer", "description": "Optional timeout"},
        },
        required=["command"],
    )
    spec = td.to_openai_spec(strict=False)
    assert spec["function"]["parameters"]["required"] == ["command"]


def test_non_strict_defaults_to_first_param():
    """Non-strict without explicit required: only first param is required."""
    td = ToolDefinition(
        name="basic",
        description="Basic tool",
        parameters={
            "query": {"type": "string", "description": "Search query"},
            "num": {"type": "integer", "description": "Optional count"},
        },
    )
    spec = td.to_openai_spec(strict=False)
    assert spec["function"]["parameters"]["required"] == ["query"]


class TestExpandAllowedTools:
    def test_none_returns_none(self):
        assert expand_allowed_tools(None) is None

    @pytest.mark.parametrize(
        ("allowed", "expected_present"),
        [
            (["file_manager"], "file_manager"),
            (["bash_"], "bash_manager"),
            (["completely_unknown_tool"], "completely_unknown_tool"),
            (["file_manager", "bash_"], "file_manager"),
        ],
        ids=["exact_match", "prefix_expands", "unknown_passes_through", "mixed_exact_and_prefix"],
    )
    def test_expands(self, allowed, expected_present):
        result = expand_allowed_tools(allowed)
        assert expected_present in result


class TestToolMatchesAllowlist:
    @pytest.mark.parametrize(
        ("tool", "allowlist", "expected"),
        [
            ("file_manager", ["file_manager", "git_manager"], True),
            ("file_manager", ["file_", "git_manager"], True),
            ("mcp:browser_navigate", ["browser"], True),
            ("mcp_browser_navigate", ["browser"], True),
            ("code_exec", ["file_manager", "git_manager"], False),
        ],
        ids=["exact_match", "prefix_match", "mcp_match", "sanitized_mcp_match", "no_match"],
    )
    def test_matches(self, tool, allowlist, expected):
        assert tool_matches_allowlist(tool, allowlist) is expected


def test_active_projects_base_uses_active_workspace(monkeypatch, tmp_path):
    """El helper de proyectos del maestro usa el workspace activo, no 'main'."""
    from unittest.mock import MagicMock

    from desktop.services.project_service import projects_base

    ws_mock = MagicMock()
    ws_mock.current = "prueba9"
    monkeypatch.setattr("core.workspaces.workspaces_instance", ws_mock)
    monkeypatch.setattr("core.path_resolver.paths.memory_dir", lambda ws: tmp_path / ws)

    base = projects_base()
    assert base == tmp_path / "prueba9" / "code_projects"


def test_no_nested_schema_keys_in_parameters():
    """`parameters` es el dict PLANO de properties — ninguna ToolDefinition
    puede anidar type/properties/required dentro de parameters (rompe to_openai_spec
    y el strict mode de DeepSeek)."""
    forbidden = {"type", "properties", "required"}
    for name, td in TOOL_DEFINITIONS.items():
        nested = forbidden & set(td.parameters.keys())
        assert not nested, f"{name}: claves de schema anidadas en parameters: {nested}"


def test_memory_inspector_spec_is_flat_and_callable():
    """memory_inspector aplanado genera un spec OpenAI válido."""
    spec = TOOL_DEFINITIONS["memory_inspector"].to_openai_spec()
    props = spec["function"]["parameters"]["properties"]
    assert set(props.keys()) == {"action", "key", "confirm_delete"}
    assert props["action"]["enum"] == ["list", "read", "delete"]
    assert "action" in spec["function"]["parameters"]["required"]


def test_development_workflow_allowlist_includes_memory_inspector():
    """memory_inspector expuesto en la allowlist del workflow development."""
    from core.path_resolver import paths

    dev = (paths.templates_workflows_dir() / "development.yaml").read_text(encoding="utf-8")
    assert "memory_inspector" in dev
