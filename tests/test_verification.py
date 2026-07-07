# tests/test_verification.py
"""Tests para el módulo de verificación del workflow."""

from unittest.mock import patch

import pytest

from orchestration.executor.verify import (
    _check_test_file_exists,
    _extract_and_validate_actions,
)


class TestExtractAndValidateActions:
    def test_extracts_valid_actions(self):
        plan = {
            "actions": [
                {"tool": "file_manager", "action": "write", "params": {"path": "test.py"}},
                {"tool": "test_runner", "action": "run", "params": {}},
            ]
        }
        result = _extract_and_validate_actions(plan, ["file_manager", "test_runner"])
        assert len(result) == 2
        assert result[0]["tool"] == "file_manager"

    def test_filters_disallowed_tools(self):
        plan = {
            "actions": [
                {"tool": "file_manager", "action": "write"},
                {"tool": "bash_manager", "action": "exec"},
            ]
        }
        result = _extract_and_validate_actions(plan, ["file_manager"])
        assert len(result) == 1
        assert result[0]["tool"] == "file_manager"

    def test_empty_plan_returns_empty(self):
        result = _extract_and_validate_actions({}, [])
        assert result == []

    def test_non_dict_plan_returns_empty(self):
        result = _extract_and_validate_actions(None, [])
        assert result == []


class TestCheckTestFileExists:
    @pytest.mark.asyncio
    async def test_finds_test_file(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "test_app.py").write_text("def test_ok(): pass")
        with patch("core.path_resolver.paths.memory_dir", return_value=tmp_path):
            result = await _check_test_file_exists("subdir", "main")
            assert result is True

    @pytest.mark.asyncio
    async def test_no_test_files(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "app.py").write_text("print('ok')")
        with patch("core.path_resolver.paths.memory_dir", return_value=tmp_path):
            result = await _check_test_file_exists("subdir", "main")
            assert result is False
