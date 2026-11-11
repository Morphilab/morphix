# tests/test_orchestrator_parse.py
"""Tests del parsing de comandos directos de tool (WorkflowOrchestrator)."""


class TestParseDirectToolCommand:
    def test_parse_simple_tool_command(self):
        from unittest.mock import patch

        from orchestration.workflows.orchestrator import _parse_direct_tool_command

        with patch("tools.registry.tools_registry.get_tool", return_value={"name": "file_manager"}):
            result = _parse_direct_tool_command("file_manager: read, path=test.txt")
        assert result is not None
        assert result["tool_name"] == "file_manager"
        assert result["action"] == "read"
        assert result["params"] == {"path": "test.txt"}

    def test_parse_tool_command_with_multiple_params(self):
        from unittest.mock import patch

        from orchestration.workflows.orchestrator import _parse_direct_tool_command

        with patch("tools.registry.tools_registry.get_tool", return_value={"name": "git_manager"}):
            result = _parse_direct_tool_command(
                "git_manager: commit, message='fix bug', files='app.py'"
            )
        assert result is not None
        assert result["tool_name"] == "git_manager"
        assert result["action"] == "commit"
        assert result["params"]["message"] == "fix bug"
        assert result["params"]["files"] == "app.py"

    def test_parse_rejects_normal_conversation(self):
        from orchestration.workflows.orchestrator import _parse_direct_tool_command

        result = _parse_direct_tool_command("Hola, ¿cómo estás?")
        assert result is None

    def test_parse_rejects_question(self):
        from orchestration.workflows.orchestrator import _parse_direct_tool_command

        result = _parse_direct_tool_command("¿Puedes escribir una función?")
        assert result is None
