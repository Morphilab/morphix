# tests/test_test_runner.py
"""Tests para el ejecutor de tests pytest."""

import os
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

    def test_sin_resumen_no_hay_medicion(self):
        """Sin línea de resumen parseable no hay medición: claves ausentes,
        no ceros (los ceros hacían creer al until que había medido)."""
        counts = _parse_pytest_counts("no test output here")
        assert counts == {}


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
        proc_mock.pid = os.getpid()  # mock seguro: nunca dispara killpg(1)
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
        proc_mock.pid = os.getpid()  # mock seguro: nunca dispara killpg(1)
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
async def test_timeout_kills_process(tmp_path):
    """Al exceder el timeout, el subproceso pytest debe matarse (sin huérfanos)."""
    from unittest.mock import MagicMock

    test_file = tmp_path / "test_slow.py"
    test_file.write_text("def test_slow(): assert True\n")

    with patch("tools.test_runner.asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.returncode = 0
        proc_mock.communicate = AsyncMock(side_effect=TimeoutError)
        proc_mock.wait = AsyncMock()
        proc_mock.kill = MagicMock()
        proc_mock.pid = os.getpid()  # mismo pgid que el test → fallback proc.kill()
        mock_exec.return_value = proc_mock

        with patch("tools.test_runner.paths.memory_dir", return_value=tmp_path.parent):
            result = await _test_runner_tool(
                file_path=str(test_file),
                workspace="main",
                timeout=5,
            )

        proc_mock.kill.assert_called_once()
        assert result["success"] is False
        assert "timeout" in result["output"].lower()


@pytest.mark.asyncio
async def test_sin_conteos_no_fabrica_ceros(tmp_path):
    """Salida sin resumen (p.ej. error de colección): el resultado omite
    las claves de conteo — el consumidor distingue ausencia de cero."""
    (tmp_path / "empty_test.py").write_text("def test_x(): assert True\n")
    with patch("tools.test_runner.asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.pid = os.getpid()  # mock seguro: nunca dispara killpg(1)
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
        for clave in ("passed_count", "failed_count", "error_count", "total_count"):
            assert clave not in result, clave


@pytest.mark.asyncio
async def test_cmd_includes_rootdir(tmp_path):
    """El comando pytest incluye --rootdir del proyecto (no usa la config de morphix)."""
    (tmp_path / "test_x.py").write_text("def test_x(): assert True\n")
    captured: dict = {}

    async def mock_create_subprocess(*a, **kw):
        captured["cmd"] = a
        proc_mock = AsyncMock()
        proc_mock.pid = os.getpid()  # mock seguro: nunca dispara killpg(1)
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


class TestMetaTestGuard:
    @pytest.mark.asyncio
    async def test_meta_test_con_subprocess_pytest_se_rechaza(self, tmp_path, monkeypatch):
        marker = tmp_path / "test_meta.ran"
        (tmp_path / "test_meta.py").write_text(
            f"""
import os, subprocess, sys
from pathlib import Path

MARKER = Path({str(marker)!r})

def test_meta():
    MARKER.write_text("ran")
    depth = int(os.environ.get("META_DEPTH", "0"))
    if depth >= 2:
        return
    env = {{**os.environ, "META_DEPTH": str(depth + 1)}}
    subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-q", "-p", "no:cacheprovider"],
        env=env,
        timeout=10,
    )
""",
            encoding="utf-8",
        )
        monkeypatch.setattr("tools.test_runner.paths.memory_dir", lambda ws: tmp_path)
        result = await _test_runner_tool(file_path="test_meta.py", workspace="main", timeout=10)
        assert result["success"] is False
        assert "pytest" in result["output"].lower()
        assert not marker.exists(), "el meta-test se ejecutó antes del guard"

    @pytest.mark.asyncio
    async def test_pytest_main_se_rechaza(self, tmp_path, monkeypatch):
        # Rama muerta: el guard escanea el source; no ejecutamos pytest.main
        # dentro de un test (eso sería la recursión que el guard previene).
        (tmp_path / "test_main.py").write_text(
            "import pytest\n\n"
            "def test_x():\n"
            "    if False:\n"
            "        pytest.main([__file__])\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("tools.test_runner.paths.memory_dir", lambda ws: tmp_path)
        result = await _test_runner_tool(file_path="test_main.py", workspace="main", timeout=10)
        assert result["success"] is False
        assert "pytest.main" in result["output"] or "suite" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_subprocess_sin_pytest_no_se_bloquea(self, tmp_path, monkeypatch):
        (tmp_path / "test_cli.py").write_text(
            """
import subprocess, sys

def test_echo():
    r = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True)
    assert r.stdout.strip() == "ok"
""",
            encoding="utf-8",
        )
        monkeypatch.setattr("tools.test_runner.paths.memory_dir", lambda ws: tmp_path)
        result = await _test_runner_tool(file_path="test_cli.py", workspace="main", timeout=10)
        assert result["success"] is True


class TestCountsNoConfundenNombresDeTest:
    """El parser no puede leer "1 PASSED" de un test llamado `...latin1`."""

    def test_nombre_de_test_terminado_en_digito_no_gana_al_resumen(self):
        output = (
            "tests/test_x.py::test_count_file_encoding_latin1 PASSED   [ 54%]\n"
            "tests/test_x.py::test_otro PASSED                          [100%]\n"
            "\n"
            "============================= 101 passed in 1.45s ==============================\n"
        )
        counts = _parse_pytest_counts(output)
        assert counts["passed_count"] == 101
        assert counts["total_count"] == 101

    def test_nombre_con_digito_y_failed_no_falsea_el_conteo(self):
        output = (
            "tests/test_x.py::test_paso10 FAILED                       [ 50%]\n"
            "tests/test_x.py::test_ok PASSED                           [100%]\n"
            "\n"
            "========================= 1 failed, 1 passed in 0.20s =========================\n"
        )
        counts = _parse_pytest_counts(output)
        assert counts["failed_count"] == 1
        assert counts["passed_count"] == 1
        assert counts["total_count"] == 2

    def test_un_solo_test_real_sigue_midiendo_uno(self):
        output = "tests/test_x.py::test_unico PASSED [100%]\n\n1 passed in 0.01s\n"
        counts = _parse_pytest_counts(output)
        assert counts["passed_count"] == 1
        assert counts["total_count"] == 1


@pytest.mark.asyncio
async def test_cmd_incluye_timeout_por_test(tmp_path):
    """pytest-timeout corta un test colgado por test (30s default)."""
    test_file = tmp_path / "test_ok.py"
    test_file.write_text("def test_ok(): assert True\n")

    with patch("tools.test_runner.asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.pid = os.getpid()
        proc_mock.returncode = 0
        proc_mock.communicate = AsyncMock(return_value=(b"1 passed in 0.1s\n", b""))

        async def mock_create_subprocess(*a, **kw):
            return proc_mock

        mock_exec.side_effect = mock_create_subprocess

        with patch("tools.test_runner.paths.memory_dir", return_value=tmp_path.parent):
            result = await _test_runner_tool(file_path=str(test_file), workspace="main")

        cmd = mock_exec.call_args.args
        assert any(str(x).startswith("--timeout=") for x in cmd)
        assert "pytest_timeout" not in cmd  # auto-carga (evita doble registro)
        assert result["success"] is True


@pytest.mark.asyncio
async def test_test_colgado_se_reporta_failed_medido(tmp_path):
    """Un test que cuelga termina en failed/total medidos, no en timeout global."""
    test_file = tmp_path / "test_cuelga.py"
    test_file.write_text(
        "import time\n\n\ndef test_cuelga():\n    while True:\n        time.sleep(0.05)\n"
    )

    with (
        patch("tools.test_runner.paths.memory_dir", return_value=tmp_path.parent),
        patch("core.config.settings.pytest_timeout_s", 1),
    ):
        result = await _test_runner_tool(file_path=str(test_file), workspace="main")

    assert result["success"] is False
    assert result["failed_count"] == 1
    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_gate_de_directorio_inspecciona_sus_tests(tmp_path):
    """El gate de la suite (`file_path: tests`) expande el dir al meta-test
    guard: un test recursivo dentro del dir no puede pasar."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_recursivo.py").write_text(
        "import subprocess, sys\n\n\ndef test_x():\n"
        "    subprocess.run([sys.executable, '-m', 'pytest'])\n"
    )

    with patch("tools.test_runner.paths.memory_dir", return_value=tmp_path):
        result = await _test_runner_tool(file_path="tests", workspace="main")

    assert result["success"] is False
    assert "recursión" in result["output"]
