# tests/test_test_runner.py
"""Tests para el ejecutor de tests pytest."""

from unittest.mock import AsyncMock, patch

import pytest

from tools.test_runner import _parse_pytest_counts, _test_runner_tool


class TestParsePytestCounts:
    def test_parses_all_passed(self):
        output = "5 passed in 1.23s"
        counts = _parse_pytest_counts(output)
        assert counts["passed_count"] == 5
        assert counts["failed_count"] == 0
        assert counts["error_count"] == 0

    def test_parses_mixed_results(self):
        output = "3 passed, 1 failed, 2 errors in 0.5s"
        counts = _parse_pytest_counts(output)
        assert counts["passed_count"] == 3
        assert counts["failed_count"] == 1
        assert counts["error_count"] == 2

    def test_parses_case_insensitive(self):
        output = "10 PASSED, 2 FAILED in 5s"
        counts = _parse_pytest_counts(output)
        assert counts["passed_count"] == 10
        assert counts["failed_count"] == 2

    def test_returns_zeros_for_no_match(self):
        counts = _parse_pytest_counts("no test output here")
        assert counts["passed_count"] == 0
        assert counts["failed_count"] == 0


@pytest.mark.asyncio
async def test_path_traversal_blocked():
    """Verifica que rutas con ../ fuera del workspace se bloqueen."""
    from tools.test_runner import _test_runner_tool

    result = await _test_runner_tool(
        file_path="../../etc/passwd",
        workspace="test_ws",
        project_root=None,
    )
    assert result["success"] is False
    assert "fuera del workspace" in result["output"]


@pytest.mark.asyncio
async def test_file_not_found(tmp_path):
    """Verifica error cuando el archivo de test no existe."""
    from unittest.mock import patch

    from tools.test_runner import _test_runner_tool

    with patch("core.path_resolver.paths.memory_dir", return_value=tmp_path):
        result = await _test_runner_tool(
            file_path="nonexistent_test.py",
            workspace="main",
        )
        assert result["success"] is False
        assert "no encontrado" in result["output"].lower()


@pytest.mark.asyncio
async def test_all_pass_with_warnings_is_success(tmp_path):
    """Tests pasando con returncode != 0 (warnings) deben ser éxito."""
    test_file = tmp_path / "test_ok.py"
    test_file.write_text("def test_ok(): assert True\n")

    with patch("tools.test_runner.asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.return_value = proc_mock
        proc_mock.returncode = 1
        proc_mock.communicate = AsyncMock(return_value=(b"2 passed in 0.50s\n", b""))

        async def mock_create_subprocess(*a, **kw):
            return proc_mock

        mock_exec.side_effect = mock_create_subprocess

        with patch("tools.test_runner.paths.memory_dir", return_value=tmp_path.parent):
            result = await _test_runner_tool(
                file_path=str(test_file),
                workspace="main",
            )

        assert result["success"] is True
        assert result["passed_count"] == 2
        assert result["failed_count"] == 0


@pytest.mark.asyncio
async def test_some_fail_is_failure(tmp_path):
    """Tests con algunos fallos reales deben ser fallo."""
    test_file = tmp_path / "test_fail.py"
    test_file.write_text("def test_fail(): assert False\n")

    with patch("tools.test_runner.asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.return_value = proc_mock
        proc_mock.returncode = 1
        proc_mock.communicate = AsyncMock(return_value=(b"1 passed, 1 failed in 0.40s\n", b""))

        async def mock_create_subprocess(*a, **kw):
            return proc_mock

        mock_exec.side_effect = mock_create_subprocess

        with patch("tools.test_runner.paths.memory_dir", return_value=tmp_path.parent):
            result = await _test_runner_tool(
                file_path=str(test_file),
                workspace="main",
            )

        assert result["success"] is False


@pytest.mark.asyncio
async def test_no_tests_run_is_failure(tmp_path):
    """Sin tests ejecutados (colección vacía) debe ser fallo aunque returncode=0."""
    with patch("tools.test_runner.asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.return_value = proc_mock
        proc_mock.returncode = 1
        proc_mock.communicate = AsyncMock(return_value=(b"collected 0 items\n", b""))

        async def mock_create_subprocess(*a, **kw):
            return proc_mock

        mock_exec.side_effect = mock_create_subprocess

        with patch("tools.test_runner.paths.memory_dir", return_value=tmp_path):
            result = await _test_runner_tool(
                file_path="empty_test.py",
                workspace="main",
            )

        assert result["success"] is False


@pytest.mark.asyncio
async def test_cmd_includes_rootdir(tmp_path):
    """El comando pytest incluye --rootdir del proyecto (no usa la config de morphix)."""
    (tmp_path / "test_x.py").write_text("def test_x(): assert True\n")
    captured: dict = {}

    async def mock_create_subprocess(*a, **kw):
        captured["cmd"] = a
        proc_mock = AsyncMock()
        proc_mock.returncode = 0
        proc_mock.communicate = AsyncMock(return_value=(b"1 passed in 0.1s\n", b""))
        return proc_mock

    with (
        patch(
            "tools.test_runner.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ),
        patch("tools.test_runner.paths.memory_dir", return_value=tmp_path),
    ):
        await _test_runner_tool(file_path="test_x.py", workspace="main")

    assert any(str(c).startswith("--rootdir=") for c in captured["cmd"])
    assert "no:cacheprovider" in captured["cmd"]
