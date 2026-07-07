"""Tests for core/mcp/adapter.py — dict conversion functions."""

from core.mcp.adapter import (
    mcp_result_to_morphix,
    mcp_tool_to_morphix_params,
    morphix_to_mcp_tool,
)


class TestMorphixToMcpTool:
    def test_basic_conversion(self):
        result = morphix_to_mcp_tool(
            {
                "name": "my_tool",
                "description": "Does stuff",
                "parameters": {"x": {"type": "string"}},
                "required": ["x"],
            }
        )
        assert result["name"] == "my_tool"
        assert result["inputSchema"]["type"] == "object"
        assert result["inputSchema"]["properties"] == {"x": {"type": "string"}}
        assert result["inputSchema"]["required"] == ["x"]

    def test_missing_optional_fields(self):
        result = morphix_to_mcp_tool({"name": "bare"})
        assert result["inputSchema"]["properties"] == {}
        assert result["inputSchema"]["required"] == []


class TestMcpToolToMorphixParams:
    def test_basic_conversion(self):
        result = mcp_tool_to_morphix_params(
            {
                "name": "mcp_tool",
                "description": "desc",
                "inputSchema": {
                    "properties": {"a": {"type": "int"}},
                    "required": ["a"],
                },
            }
        )
        assert result["name"] == "mcp_tool"
        assert result["parameters"] == {"a": {"type": "int"}}
        assert result["required"] == ["a"]

    def test_missing_input_schema(self):
        result = mcp_tool_to_morphix_params({"name": "bare"})
        assert result["parameters"] == {}
        assert result["required"] == []


class TestMcpResultToMorphix:
    def test_success_text(self):
        result = mcp_result_to_morphix({"content": [{"type": "text", "text": "hello"}]})
        assert result["success"] is True
        assert result["output"] == "hello"

    def test_error_result(self):
        result = mcp_result_to_morphix(
            {"isError": True, "content": [{"type": "text", "text": "fail"}]}
        )
        assert result["success"] is False
        assert result["output"] == "fail"

    def test_image_content(self):
        result = mcp_result_to_morphix(
            {"content": [{"type": "image", "data": "abc", "mimeType": "image/png"}]}
        )
        assert "image: image/png" in result["output"]

    def test_empty_content(self):
        result = mcp_result_to_morphix({})
        assert result["success"] is True
        assert result["output"] == ""
