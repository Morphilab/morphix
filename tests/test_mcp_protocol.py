# tests/test_mcp_protocol.py
"""Tests for MCP JSON-RPC protocol framing."""

import asyncio
import json

import pytest

from core.mcp.protocol import (
    build_error,
    build_notification,
    build_request,
    build_response,
    get_id,
    is_notification,
    is_response,
)


class TestProtocolBuilders:
    def test_build_request(self):
        req = build_request(1, "tools/list", {"cursor": None})
        assert req["jsonrpc"] == "2.0"
        assert req["id"] == 1
        assert req["method"] == "tools/list"
        assert req["params"] == {"cursor": None}

    def test_build_request_default_params(self):
        req = build_request(2, "tools/list")
        assert req["params"] == {}

    def test_build_notification(self):
        notif = build_notification("notifications/initialized")
        assert "id" not in notif
        assert notif["method"] == "notifications/initialized"
        assert notif["params"] == {}

    def test_build_response(self):
        resp = build_response(1, {"tools": []})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert resp["result"] == {"tools": []}

    def test_build_error(self):
        err = build_error(1, -32601, "Method not found")
        assert err["jsonrpc"] == "2.0"
        assert err["id"] == 1
        assert err["error"]["code"] == -32601
        assert err["error"]["message"] == "Method not found"

    def test_is_notification(self):
        assert is_notification({"jsonrpc": "2.0", "method": "test"}) is True
        assert is_notification({"jsonrpc": "2.0", "id": 1, "method": "test"}) is False

    def test_is_response(self):
        assert is_response({"jsonrpc": "2.0", "id": 1, "result": {}}) is True
        assert is_response({"jsonrpc": "2.0", "id": 1, "error": {}}) is True
        assert is_response({"jsonrpc": "2.0", "id": 1, "method": "test"}) is False

    def test_get_id(self):
        assert get_id({"id": 42, "method": "test"}) == 42
        assert get_id({"method": "test"}) is None


class TestMCPAdapter:
    def test_morphix_to_mcp_tool(self):
        from core.mcp.adapter import morphix_to_mcp_tool

        morphix_tool = {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"path": {"type": "string", "description": "File path"}},
            "required": ["path"],
        }
        mcp = morphix_to_mcp_tool(morphix_tool)
        assert mcp["name"] == "read_file"
        assert "inputSchema" in mcp
        assert mcp["inputSchema"]["type"] == "object"
        assert "path" in mcp["inputSchema"]["properties"]
        assert mcp["inputSchema"]["required"] == ["path"]

    def test_mcp_tool_to_morphix_params(self):
        from core.mcp.adapter import mcp_tool_to_morphix_params

        mcp_tool = {
            "name": "search",
            "description": "Search code",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
        params = mcp_tool_to_morphix_params(mcp_tool)
        assert params["name"] == "search"
        assert params["parameters"]["query"]["type"] == "string"
        assert params["required"] == ["query"]

    def test_mcp_result_to_morphix_success(self):
        from core.mcp.adapter import mcp_result_to_morphix

        result = mcp_result_to_morphix({"content": [{"type": "text", "text": "Hello world"}]})
        assert result["success"] is True
        assert result["output"] == "Hello world"

    def test_mcp_result_to_morphix_error(self):
        from core.mcp.adapter import mcp_result_to_morphix

        result = mcp_result_to_morphix(
            {"content": [{"type": "text", "text": "Permission denied"}], "isError": True}
        )
        assert result["success"] is False
        assert "Permission denied" in result["output"]

    def test_mcp_result_to_morphix_mixed_content(self):
        from core.mcp.adapter import mcp_result_to_morphix

        result = mcp_result_to_morphix(
            {
                "content": [
                    {"type": "text", "text": "Line 1"},
                    {"type": "image", "mimeType": "image/png"},
                    {"type": "text", "text": "Line 3"},
                ]
            }
        )
        assert result["success"] is True
        assert "Line 1" in result["output"]
        assert "[image:" in result["output"]
        assert "Line 3" in result["output"]


class TestReadWriteMessage:
    @pytest.mark.asyncio
    async def test_read_request(self):
        from core.mcp.protocol import read_message

        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        reader = asyncio.StreamReader()
        encoded = json.dumps(msg).encode("utf-8") + b"\n"
        reader.feed_data(encoded)
        reader.feed_eof()

        result = await read_message(reader)
        assert result == msg

    @pytest.mark.asyncio
    async def test_read_response(self):
        from core.mcp.protocol import build_response, read_message

        msg = build_response(5, {"tools": [{"name": "test"}]})
        reader = asyncio.StreamReader()
        encoded = json.dumps(msg).encode("utf-8") + b"\n"
        reader.feed_data(encoded)
        reader.feed_eof()

        result = await read_message(reader)
        assert result["id"] == 5
        assert result["result"]["tools"][0]["name"] == "test"

    @pytest.mark.asyncio
    async def test_read_closed_stream_raises(self):
        from core.mcp.protocol import read_message

        reader = asyncio.StreamReader()
        reader.feed_eof()

        with pytest.raises(ConnectionError, match="closed"):
            await read_message(reader)
