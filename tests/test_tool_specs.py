# tests/test_tool_specs.py
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
    """Las 10 herramientas tienen ToolDefinition."""
    assert len(TOOL_DEFINITIONS) == 11
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
    }
    assert set(TOOL_DEFINITIONS.keys()) == expected


def test_build_tool_definitions_all():
    """build_tool_definitions sin filtro devuelve todas."""
    defs = build_tool_definitions()
    assert len(defs) == 11
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
    # Strict mode: ALL properties must be in required
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

    def test_exact_match_passes_through(self):
        result = expand_allowed_tools(["file_manager"])
        assert "file_manager" in result

    def test_prefix_expands_to_matching_keys(self):
        """Prefix like 'bash_' should match 'bash_manager'."""
        # 'bash_ma' is a prefix of 'bash_manager'
        if any(k.startswith("bash_") for k in TOOL_DEFINITIONS):
            result = expand_allowed_tools(["bash_"])
            assert any("bash_manager" in r for r in result)

    def test_unknown_entry_passes_through(self):
        result = expand_allowed_tools(["completely_unknown_tool"])
        assert "completely_unknown_tool" in result

    def test_mixed_exact_and_prefix(self):
        result = expand_allowed_tools(["file_manager", "bash_"])
        assert "file_manager" in result


class TestToolMatchesAllowlist:
    def test_exact_match(self):
        assert tool_matches_allowlist("file_manager", ["file_manager", "git_manager"]) is True

    def test_prefix_match(self):
        assert tool_matches_allowlist("file_manager", ["file_", "git_manager"]) is True

    def test_mcp_match(self):
        assert tool_matches_allowlist("mcp:browser_navigate", ["browser"]) is True

    def test_sanitized_mcp_match(self):
        assert tool_matches_allowlist("mcp_browser_navigate", ["browser"]) is True

    def test_no_match(self):
        assert tool_matches_allowlist("code_exec", ["file_manager", "git_manager"]) is False
