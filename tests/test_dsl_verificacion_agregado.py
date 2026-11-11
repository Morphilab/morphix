# tests/test_dsl_verificacion_agregado.py
"""La verificación del agregado verifica SOLO lo que el run tocó.

Antes corría `test_runner` sobre `tests/` completo: en un repo con tests
preexistentes en rojo, toda entrega quedaba Parcial por culpa ajena. Ahora el
preset recolecta los artefactos del run (plugin ``files_del_run``) y
``test_runner`` acepta varias rutas (``file_paths``).

Trade-off documentado: lo que el run rompió SIN tocar tampoco se ve — la
verificación del agregado acotada es "lo mío está verde", no "la suite está
verde".
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import orchestration.dsl.plugins  # noqa: F401 — registro de primitivas
from orchestration.dsl.plugins import _files_del_run
from tools.test_runner import _test_runner_tool


class _RuntimeConProperty:
    """Runtime estilo ProductionRuntime: files_written como property."""

    @property
    def files_written(self):
        return ["src/main.py", "tests/test_a.py", "tests/sub/test_b.py"]


class _RuntimeConMetodo:
    """Runtime estilo Fake: files_written como método."""

    def files_written(self):
        return ["tests/test_a.py"]


class _RuntimeSinNada:
    """Runtime sin exponer archivos (fakes mínimos)."""


async def _corre_plugin(runtime, params) -> SimpleNamespace:
    state = SimpleNamespace(vars={})
    step = SimpleNamespace(params=params)
    await _files_del_run(step, state, runtime, "prueba")
    return state


class TestFilesDelRun:
    @pytest.mark.asyncio
    async def test_filtra_por_glob_con_profundidad(self):
        state = await _corre_plugin(_RuntimeConProperty(), {"out": "sel", "glob": "tests/**/*.py"})
        assert state.vars["sel"] == ["tests/test_a.py", "tests/sub/test_b.py"]

    @pytest.mark.asyncio
    async def test_acepta_runtime_con_metodo_o_property(self):
        state = await _corre_plugin(_RuntimeConMetodo(), {"out": "sel", "glob": "tests/*.py"})
        assert state.vars["sel"] == ["tests/test_a.py"]

    @pytest.mark.asyncio
    async def test_sin_coincidencias_deja_lista_vacia(self):
        state = await _corre_plugin(_RuntimeConProperty(), {"out": "sel", "glob": "docs/**/*.md"})
        assert state.vars["sel"] == []

    @pytest.mark.asyncio
    async def test_runtime_sin_files_written_es_lista_vacia(self):
        """Fakes mínimos: el consumidor (test_runner) fail-fast por
        sin-medición — nunca hereda una verificación global por accidente."""
        state = await _corre_plugin(_RuntimeSinNada(), {"out": "sel", "glob": "tests/**/*.py"})
        assert state.vars["sel"] == []

    @pytest.mark.asyncio
    async def test_params_incompletos_fallan_loud(self):
        from orchestration.dsl.engine import EngineError

        with pytest.raises(EngineError):
            await _corre_plugin(_RuntimeConProperty(), {"out": "sel"})


class TestTestRunnerMultiplesRutas:
    @pytest.mark.asyncio
    async def test_varios_archivos_en_una_sola_pasada(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text("def test_a(): assert True\n")
        (tests / "test_b.py").write_text("def test_b(): assert True\n")

        with patch("core.path_resolver.paths.memory_dir", return_value=tmp_path):
            res = await _test_runner_tool(
                file_paths=["tests/test_a.py", "tests/test_b.py"], workspace="main"
            )
        assert res["passed_count"] == 2
        assert res["success"] is True

    @pytest.mark.asyncio
    async def test_una_ruta_inexistente_fail_fast_sin_medicion(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("def test_a(): assert True\n")

        with patch("core.path_resolver.paths.memory_dir", return_value=tmp_path):
            res = await _test_runner_tool(
                file_paths=["tests/test_a.py", "tests/test_fantasma.py"], workspace="main"
            )
        assert res["success"] is False
        assert "no encontrado" in res["output"].lower()
        assert "passed_count" not in res, "sin medición: no se fabrican conteos"

    @pytest.mark.asyncio
    async def test_lista_vacia_es_error_claro(self, tmp_path):
        with patch("core.path_resolver.paths.memory_dir", return_value=tmp_path):
            res = await _test_runner_tool(file_paths=[], workspace="main")
        assert res["success"] is False
        assert "sin rutas" in res["output"]

    @pytest.mark.asyncio
    async def test_cadena_separada_por_comas_se_tolerar(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text("def test_a(): assert True\n")
        (tests / "test_b.py").write_text("def test_b(): assert True\n")

        with patch("core.path_resolver.paths.memory_dir", return_value=tmp_path):
            res = await _test_runner_tool(
                file_paths="tests/test_a.py, tests/test_b.py", workspace="main"
            )
        assert res["passed_count"] == 2
        assert res["success"] is True
