"""Load MCP server configurations from JSON files."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    tools_prefix: str = ""  # prefix for tool names: "mcp:<prefix>.<tool>"


def _load_file(filepath: Path) -> list[MCPServerConfig]:
    if not filepath.exists():
        return []
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Invalid MCP config {filepath}: {e}")
        return []

    if not isinstance(data, list):
        logger.warning(f"MCP config {filepath} must be a JSON array")
        return []

    servers = []
    for item in data:
        if not isinstance(item, dict) or "command" not in item:
            continue
        name = str(item.get("name", item["command"]))
        tools_prefix = str(item.get("tools_prefix", name))
        servers.append(
            MCPServerConfig(
                name=name,
                command=item["command"],
                args=item.get("args", []),
                env=item.get("env", {}),
                enabled=item.get("enabled", True),
                tools_prefix=tools_prefix,
            )
        )
    return servers


def load_mcp_servers(workspace: str | None = None) -> list[MCPServerConfig]:
    """Load MCP server configs. Workspace-local overrides global."""
    from core.path_resolver import paths

    servers: dict[str, MCPServerConfig] = {}

    # Global config
    global_file = Path(__file__).parent.parent.parent / "mcp_servers.json"
    for cfg in _load_file(global_file):
        servers[cfg.name] = cfg

    # Workspace-local config (overrides same-named servers)
    if workspace:
        ws_file = paths.mcp_servers_file(workspace)
        for cfg in _load_file(ws_file):
            servers[cfg.name] = cfg

    return [s for s in servers.values() if s.enabled]
