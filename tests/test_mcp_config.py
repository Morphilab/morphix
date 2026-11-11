"""Tests for core/mcp/config.py — MCPServerConfig, _load_file, load_mcp_servers."""

import json
import tempfile
from pathlib import Path

from core.mcp.config import MCPServerConfig, _load_file


class TestMCPServerConfig:
    def test_defaults(self):
        cfg = MCPServerConfig(name="test", command="node")
        assert cfg.name == "test"
        assert cfg.command == "node"
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.enabled is True
        assert cfg.tools_prefix == ""

    def test_full_config(self):
        cfg = MCPServerConfig(
            name="srv",
            command="python",
            args=["-m", "srv"],
            env={"KEY": "val"},
            enabled=False,
            tools_prefix="pref",
        )
        assert cfg.tools_prefix == "pref"
        assert cfg.enabled is False


class TestLoadFile:
    def test_missing_file_returns_empty(self):
        result = _load_file(Path("/nonexistent/path.json"))
        assert result == []

    def test_valid_json_array(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                [
                    {"command": "node", "args": ["srv.js"]},
                    {"command": "python", "name": "pysrv", "enabled": False},
                ],
                f,
            )
            f.flush()
            path = Path(f.name)

        try:
            result = _load_file(path)
            assert len(result) == 2
            assert result[0].command == "node"
            assert result[1].name == "pysrv"
            assert result[1].enabled is False
        finally:
            path.unlink()

    def test_invalid_json_returns_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            f.flush()
            path = Path(f.name)

        try:
            result = _load_file(path)
            assert result == []
        finally:
            path.unlink()

    def test_non_array_json_returns_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"key": "val"}, f)
            f.flush()
            path = Path(f.name)

        try:
            result = _load_file(path)
            assert result == []
        finally:
            path.unlink()

    def test_skips_items_without_command(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([{"no_command": 1}, {"command": "valid_cmd"}], f)
            f.flush()
            path = Path(f.name)

        try:
            result = _load_file(path)
            assert len(result) == 1
            assert result[0].command == "valid_cmd"
        finally:
            path.unlink()


class TestLoadMcpServers:
    def test_loads_global_servers(self):
        from unittest.mock import patch

        from core.mcp.config import load_mcp_servers

        with patch(
            "core.mcp.config._load_file",
            return_value=[MCPServerConfig(name="mock", command="mock")],
        ):
            result = load_mcp_servers(workspace=None)
            assert len(result) == 1
            assert result[0].name == "mock"

    def test_workspace_overrides_global(self):
        from unittest.mock import patch

        from core.mcp.config import load_mcp_servers

        global_cfg = MCPServerConfig(name="shared", command="global_cmd")
        ws_cfg = MCPServerConfig(name="shared", command="ws_cmd")

        call_count = [0]

        def mock_load_file(path):
            call_count[0] += 1
            if call_count[0] == 1:
                return [global_cfg]  # global file
            return [ws_cfg]  # workspace file

        with patch("core.mcp.config._load_file", wraps=mock_load_file):
            result = load_mcp_servers(workspace="test_ws")
            assert result[0].command == "ws_cmd"

    def test_disabled_servers_filtered(self):
        from unittest.mock import patch

        from core.mcp.config import load_mcp_servers

        with patch(
            "core.mcp.config._load_file",
            return_value=[
                MCPServerConfig(name="enabled", command="cmd", enabled=True),
                MCPServerConfig(name="disabled", command="cmd", enabled=False),
            ],
        ):
            result = load_mcp_servers(workspace=None)
            assert len(result) == 1
            assert result[0].name == "enabled"
