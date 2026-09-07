# tests/test_dsl_commit_after.py
"""Tests de commit_after en el motor DSL.

commit_after = decisión declarada del workflow: el MOTOR committea tras
completar steps cuyos ids están declarados. Best-effort: un fallo de git
jamás tira el workflow. Resume no re-committea (completed-check).
"""

from unittest.mock import AsyncMock, patch

import pytest

from orchestration.dsl.compiler import compile_workflow
from orchestration.dsl.engine import WorkflowEngine
from orchestration.dsl.validator import validate_workflow
from tests.test_dsl_engine import FakeRuntime, _compile, _doc


class _RecordingRuntime(FakeRuntime):
    def __init__(self, **over):
        super().__init__(**over)
        self.commit_calls: list[str] = []

    async def commit_after_step(self, step_id: str) -> None:
        self.commit_calls.append(step_id)


class TestValidator:
    def test_commit_after_id_inexistente(self):
        cw = compile_workflow(
            _doc([{"id": "a", "kind": "agent", "agent": "developer"}], commit_after=["fantasma"])
        )
        errores = validate_workflow(cw)
        assert any("fantasma" in e and "commit_after" in e for e in errores)

    def test_commit_after_id_nested_valido(self):
        cw = compile_workflow(
            _doc(
                [
                    {
                        "id": "ciclo",
                        "kind": "loop",
                        "max_iter": 2,
                        "body": [{"id": "interno", "kind": "agent", "agent": "developer"}],
                    }
                ],
                commit_after=["interno"],
            )
        )
        assert validate_workflow(cw) == []

    def test_commit_after_vacio_sin_errores(self):
        cw = compile_workflow(_doc([{"id": "a", "kind": "agent", "agent": "developer"}]))
        assert validate_workflow(cw) == []


class TestEngine:
    @pytest.mark.asyncio
    async def test_committea_solo_steps_declarados(self):
        rt = _RecordingRuntime()
        cw = _compile(
            [
                {"id": "rojo", "kind": "agent", "agent": "developer"},
                {"id": "verde", "kind": "agent", "agent": "developer"},
            ],
            commit_after=["verde"],
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt.commit_calls == ["verde"]

    @pytest.mark.asyncio
    async def test_step_nested_committea_por_iteracion(self):
        rt = _RecordingRuntime()
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 2,
                    "body": [{"id": "interno", "kind": "agent", "agent": "developer"}],
                }
            ],
            commit_after=["interno"],
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt.commit_calls == ["interno", "interno"]

    @pytest.mark.asyncio
    async def test_resume_no_recommittea(self):
        rt = _RecordingRuntime()
        cw = _compile(
            [
                {"id": "rojo", "kind": "agent", "agent": "developer"},
                {"id": "verde", "kind": "agent", "agent": "developer"},
                {"id": "humano", "kind": "checkpoint", "question": "¿ok?"},
            ],
            commit_after=["rojo"],
        )
        engine = WorkflowEngine(rt)
        result = await engine.run(cw, inputs={"query": "q"})
        assert result.status == "paused"
        assert rt.commit_calls == ["rojo"]  # solo el primer run

        rt.checkpoint_approved = True
        result2 = await WorkflowEngine(rt).run(cw, inputs={"query": "q"}, resume=result.snapshot)
        assert result2.status == "completed"
        # "rojo" ya estaba completado: NO se re-committea en el resume
        assert rt.commit_calls == ["rojo"]


class TestAdapter:
    def _runtime(self, **over):
        from orchestration.dsl.runtime_adapter import ProductionRuntime

        kwargs = dict(workspace="main", project_root="/tmp/proj", tools_allowed=[])
        kwargs.update(over)
        return ProductionRuntime(**kwargs)

    @pytest.mark.asyncio
    async def test_call_agent_acumula_files_written(self):
        rt = self._runtime()
        with patch(
            "orchestration.loop.execute_agent_loop",
            new_callable=AsyncMock,
            return_value={"status": "completed", "result": "ok", "files_written": ["a.py"]},
        ):
            await rt.call_agent("developer", "tarea", step_id="s1")
        assert rt._files_written == ["a.py"]

    @pytest.mark.asyncio
    async def test_commit_after_step_comitea_con_proyecto_y_archivos(self):
        rt = self._runtime()
        rt._files_written = ["a.py"]
        with patch("core.git_operations.auto_commit", new_callable=AsyncMock) as mock_commit:
            mock_commit.return_value = {"success": True}
            await rt.commit_after_step("rojo")
        assert mock_commit.await_count == 1
        kwargs = mock_commit.call_args.kwargs
        assert kwargs["project_root"] == "/tmp/proj"
        assert "rojo" in kwargs["message"]

    @pytest.mark.asyncio
    async def test_commit_after_sin_archivos_no_comitea(self):
        rt = self._runtime()
        with patch("core.git_operations.auto_commit", new_callable=AsyncMock) as mock_commit:
            await rt.commit_after_step("rojo")
        assert mock_commit.await_count == 0

    @pytest.mark.asyncio
    async def test_commit_after_sin_proyecto_no_comitea(self):
        rt = self._runtime(project_root=".")
        rt._files_written = ["a.py"]
        with patch("core.git_operations.auto_commit", new_callable=AsyncMock) as mock_commit:
            await rt.commit_after_step("rojo")
        assert mock_commit.await_count == 0

    @pytest.mark.asyncio
    async def test_commit_after_fallo_de_git_no_levanta(self):
        rt = self._runtime()
        rt._files_written = ["a.py"]
        with patch(
            "core.git_operations.auto_commit",
            new_callable=AsyncMock,
            return_value={"success": False},
        ):
            await rt.commit_after_step("rojo")  # no raise, best-effort

    @pytest.mark.asyncio
    async def test_commit_after_git_explota_no_tira_workflow(self):
        rt = self._runtime()
        rt._files_written = ["a.py"]
        with patch(
            "core.git_operations.auto_commit",
            new_callable=AsyncMock,
            side_effect=RuntimeError("git roto"),
        ):
            await rt.commit_after_step("rojo")  # no raise
