# tests/test_agent_loader.py
"""Tests for the workspace agent loader."""

from unittest.mock import patch

import yaml

from agents.loader import load_workspace_agents


def test_loader_skips_underscore_templates(tmp_path):
    """_FULL_TEMPLATE.yaml (y cualquier archivo _-prefijado) NO se registra como agente."""
    (tmp_path / "_FULL_TEMPLATE.yaml").write_text(
        yaml.safe_dump({"name": "mi_agente", "tools": []})
    )
    (tmp_path / "developer.yaml").write_text(yaml.safe_dump({"name": "developer", "tools": []}))

    registered: list[str] = []
    with (
        patch("core.path_resolver.paths.workspace_agents_dir", return_value=tmp_path),
        patch("agents.loader.agents_registry") as mock_reg,
    ):
        mock_reg.register_workspace_agent.side_effect = (
            lambda name, func, profile: registered.append(name)
        )
        load_workspace_agents("x")

    assert "developer" in registered
    assert "mi_agente" not in registered
