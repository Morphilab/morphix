# tests/test_dsl_dispatch.py
"""Tests de integración de la ruta DSL en WorkflowOrchestrator.

Parchea la infra (execute_agent_loop, safe_tool_call, finalize_workflow,
_save_paused_session, workspaces) y sirve documentos DSL inyectados —
verifica el CABLEADO dispatch→compile→validate→engine→finalize/pausa.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from orchestration.workflows.orchestrator import (
    PAUSED_MARKER,
    WorkflowOrchestrator,
)

# ── Helpers de fakes ────────────────────────────────────────────────────────


def _dsl_doc(steps: list[dict], **over) -> dict:
    base = {
        "version": 1,
        "name": "demo_dsl",
        "agents": {"allowed": ["developer"]},
        "tools": {"allowed": ["test_runner"]},
        "steps": steps,
    }
    base.update(over)
    return base


def _session(query: str = "tarea de prueba", conv_id: int | None = None):
    ctx = SimpleNamespace(
        query=query,
        active_workflow="demo_dsl",
        conversation_history=[],
        conversation_id=conv_id,
        project_root=".",
        allowed_tools=[],
        skills_enabled=False,
        is_follow_up=False,
        cancelled=False,
        workspace="main",
        last_clarification="",
        policy=None,
        force_agent=None,
    )
    events = SimpleNamespace(
        on_stream_chunk=None,
        on_system=AsyncMock(),
        on_stats=AsyncMock(),
        on_approval_required=None,
        on_system_message=AsyncMock(),
        on_assistant_message=AsyncMock(),
        on_stats_update=AsyncMock(),
        on_agent_stream=None,
    )
    ssn = SimpleNamespace(context=ctx, events=events, emitter=None)
    return ssn


@pytest.fixture
def fake_ws():
    ws = SimpleNamespace(current="main")
    with patch(
        "orchestration.workflows.orchestrator.get_global_workspaces",
        return_value=ws,
    ):
        yield ws


class TestRutaDSL:
    @pytest.mark.asyncio
    async def test_dispatch_dsl_ejecuta_y_finaliza(self, fake_ws):
        doc = _dsl_doc([{"id": "paso1", "kind": "agent", "agent": "developer", "goal": "haz"}])
        session = _session()
        with (
            patch(
                "orchestration.workflows.orchestrator.load_workflow_document",
                return_value=doc,
            ),
            patch(
                "orchestration.loop.execute_agent_loop",
                new_callable=AsyncMock,
                return_value={"status": "completed", "result": "RESULTADO FINAL"},
            ),
            patch(
                "orchestration.workflows.orchestrator.finalize_workflow",
                new_callable=AsyncMock,
            ) as mock_finalize,
        ):
            out = await WorkflowOrchestrator.run_full_workflow(session, persist=True)
        assert out == "RESULTADO FINAL"
        assert mock_finalize.await_count == 1
        kwargs = mock_finalize.call_args.kwargs
        assert kwargs["final_output"] == "RESULTADO FINAL"
        assert kwargs["task_analysis"]["primary_type"] == "dsl"

    @pytest.mark.asyncio
    async def test_dsl_con_errores_semanticos_no_ejecuta(self, fake_ws):
        bad = _dsl_doc([{"id": "x", "kind": "agent", "agent": "intruso", "goal": "haz"}])
        session = _session()
        with patch(
            "orchestration.workflows.orchestrator.load_workflow_document",
            return_value=bad,
        ):
            out = await WorkflowOrchestrator.run_full_workflow(session)
        assert "errores semánticos" in out
        assert "intruso" in out

    @pytest.mark.asyncio
    async def test_dsl_pausa_persiste_paused_session(self, fake_ws):
        doc = _dsl_doc(
            [
                {"id": "paso1", "kind": "agent", "agent": "developer", "goal": "haz"},
                {"id": "humano", "kind": "checkpoint", "question": "¿Seguimos?"},
                {"id": "paso2", "kind": "agent", "agent": "developer", "goal": "mas"},
            ]
        )
        session = _session(conv_id=42)
        with (
            patch(
                "orchestration.workflows.orchestrator.load_workflow_document",
                return_value=doc,
            ),
            patch(
                "orchestration.loop.execute_agent_loop",
                new_callable=AsyncMock,
                return_value={"status": "completed", "result": "ok"},
            ),
            patch(
                "orchestration.pauses.save_paused_session",
                new_callable=AsyncMock,
            ) as mock_save,
        ):
            out = await WorkflowOrchestrator.run_full_workflow(session)
        assert out == PAUSED_MARKER
        assert mock_save.await_count == 1
        state = mock_save.call_args.kwargs["paused_state"]
        assert state["origin"] == "dsl"
        assert state["paused_step"] == "humano"
        assert state["paused_kind"] == "checkpoint"
        assert state["snapshot"]["completed"] == ["paso1"]

    @pytest.mark.asyncio
    async def test_dsl_pausa_sin_conversacion_falla_claro(self, fake_ws):
        doc = _dsl_doc([{"id": "humano", "kind": "checkpoint", "question": "¿Seguimos?"}])
        session = _session(conv_id=None)
        with patch(
            "orchestration.workflows.orchestrator.load_workflow_document",
            return_value=doc,
        ):
            out = await WorkflowOrchestrator.run_full_workflow(session)
        assert "headless" in out


class TestResumeDSL:
    @pytest.mark.asyncio
    async def test_resume_inyecta_aprobacion_y_completa(self, fake_ws):
        doc = _dsl_doc(
            [
                {"id": "paso1", "kind": "agent", "agent": "developer", "goal": "uno"},
                {"id": "humano", "kind": "checkpoint", "question": "¿ok?"},
                {"id": "paso2", "kind": "agent", "agent": "developer", "goal": "dos"},
            ]
        )
        session = _session(conv_id=7)
        paused_data = {
            "origin": "dsl",
            "workflow": "demo_dsl",
            "query": "tarea",
            "paused_step": "humano",
            "paused_kind": "checkpoint",
            "snapshot": {
                "vars": {"query": "tarea"},
                "completed": ["paso1"],
                "loop_state": {},
                "children": {},
                "results": [{"id": "paso1", "output": "uno"}],
            },
        }
        calls = []

        async def fake_agent_loop(**kwargs):
            calls.append(kwargs.get("task"))
            return {"status": "completed", "result": "hecho"}

        with (
            patch(
                "orchestration.workflows.orchestrator.load_workflow_document",
                return_value=doc,
            ),
            patch("orchestration.loop.execute_agent_loop", side_effect=fake_agent_loop),
            patch(
                "orchestration.workflows.orchestrator.finalize_workflow",
                new_callable=AsyncMock,
            ),
        ):
            out = await WorkflowOrchestrator._resume_dsl(
                session,
                paused_data=paused_data,
                question="¿ok?",
                answer="sí, sigue",
                start_time=0.0,
            )
        assert out == "hecho"
        assert calls == ["dos"]  # paso1 skippeado por snapshot, solo paso2 corre

    @pytest.mark.asyncio
    async def test_resume_clarificacion_inyecta_respuesta(self, fake_ws):
        """Pausa clarification: la respuesta humana se inyecta en el step."""
        doc = _dsl_doc([{"id": "paso1", "kind": "agent", "agent": "developer", "goal": "haz"}])
        session = _session(conv_id=7)
        paused_data = {
            "origin": "dsl",
            "workflow": "demo_dsl",
            "query": "tarea",
            "paused_step": "paso1",
            "paused_kind": "clarification",
            "snapshot": {
                "vars": {"query": "tarea"},
                "completed": [],
                "loop_state": {},
                "children": {},
                "results": [],
            },
        }
        seen_tasks = []

        async def fake_agent_loop(**kwargs):
            seen_tasks.append(kwargs.get("task"))
            return {"status": "completed", "result": "ok"}

        with (
            patch(
                "orchestration.workflows.orchestrator.load_workflow_document",
                return_value=doc,
            ),
            patch("orchestration.loop.execute_agent_loop", side_effect=fake_agent_loop),
            patch(
                "orchestration.workflows.orchestrator.finalize_workflow",
                new_callable=AsyncMock,
            ),
        ):
            out = await WorkflowOrchestrator._resume_dsl(
                session,
                paused_data=paused_data,
                question="¿qué ORM?",
                answer="sqlmodel",
                start_time=0.0,
            )
        assert out == "ok"
        assert "sqlmodel" in seen_tasks[0]

    @pytest.mark.asyncio
    async def test_resume_workflow_enruta_origin_dsl(self, fake_ws):
        """resume_workflow delega a _resume_dsl cuando origin=dsl."""
        doc = _dsl_doc([{"id": "p", "kind": "agent", "agent": "developer", "goal": "x"}])
        session = _session(conv_id=7)
        paused_row = SimpleNamespace(
            clarification_question="¿ok?",
            clarification_answer=None,
            resolved_at=None,
            paused_state={
                "origin": "dsl",
                "workflow": "demo_dsl",
                "query": "tarea",
                "paused_step": "humano",
                "paused_kind": "checkpoint",
                "snapshot": {
                    "vars": {"query": "tarea"},
                    "completed": [],
                    "loop_state": {},
                    "children": {},
                    "results": [],
                },
            },
            created_at=0,
        )

        class FakeResult:
            def scalar(self):
                return paused_row

        class FakeDB:
            def add(self, obj):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt):
                return FakeResult()

        with (
            patch(
                "orchestration.workflows.orchestrator.get_async_session",
                return_value=FakeDB(),
            ),
            patch(
                "orchestration.workflows.orchestrator.load_workflow_document",
                return_value=doc,
            ),
            patch.object(
                WorkflowOrchestrator,
                "_resume_dsl",
                new_callable=AsyncMock,
                return_value="REANUDADO",
            ) as mock_resume,
        ):
            out = await WorkflowOrchestrator.resume_workflow(session, "sí")
        assert out == "REANUDADO"
        assert mock_resume.await_count == 1


class TestDirectToolConDSL:
    @pytest.mark.asyncio
    async def test_direct_tool_respeta_ruta(self, fake_ws):
        """Comando directo con template DSL activo: allowlist desde doc crudo."""
        doc = _dsl_doc([{"id": "p", "kind": "agent", "agent": "developer", "goal": "x"}])
        session = _session(query="test_runner: run")
        with (
            patch(
                "orchestration.workflows.orchestrator.load_workflow_document",
                return_value=doc,
            ),
            patch(
                "orchestration.workflows.orchestrator._parse_direct_tool_command",
                return_value={"tool_name": "test_runner", "action": "run", "params": {}},
            ),
            patch.object(
                WorkflowOrchestrator,
                "_run_direct_tool",
                new_callable=AsyncMock,
                return_value="TOOL EJECUTADA",
            ) as mock_direct,
        ):
            out = await WorkflowOrchestrator.run_full_workflow(session)
        assert out == "TOOL EJECUTADA"
        assert mock_direct.await_count == 1


# ── cierre honesto del run DSL ──


class TestCierreHonestoDSL:
    @pytest.mark.asyncio
    async def test_exito_emite_terminal_y_files_reales(self, fake_ws):
        doc = _dsl_doc([{"id": "paso1", "kind": "agent", "agent": "developer", "goal": "haz"}])
        session = _session(conv_id=5)
        with (
            patch(
                "core.bots_chat.canonical_owner_of",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "orchestration.workflows.orchestrator.load_workflow_document",
                return_value=doc,
            ),
            patch(
                "orchestration.loop.execute_agent_loop",
                new_callable=AsyncMock,
                return_value={
                    "status": "completed",
                    "result": "LISTO",
                    "files_written": ["saludo.py"],
                },
            ),
            patch(
                "orchestration.workflows.orchestrator.finalize_workflow",
                new_callable=AsyncMock,
            ) as mock_finalize,
        ):
            out = await WorkflowOrchestrator.run_full_workflow(session, persist=True)

        assert out == "LISTO"
        # 1) files_written REALES al finalizador (antes: [] hardcodeado)
        assert mock_finalize.call_args.kwargs["files_written"] == ["saludo.py"]
        # 2) scorecard honesto
        sc = mock_finalize.call_args.kwargs["scorecard"]
        assert sc["subtasks"] >= 1
        assert sc["completadas"] == sc["subtasks"]
        # 3) emit TERMINAL con status Completado (la GUI apaga el chip ●)
        stats = [c.args[0] for c in session.events.on_stats_update.await_args_list]
        finals = [s for s in stats if str(s.get("status", "")).startswith("Completado")]
        assert finals, f"sin emit terminal; statuses: {[s.get('status') for s in stats]}"
        final = finals[-1]
        assert final["subtasks_total"] >= 1
        assert final["subtasks_completed"] == final["subtasks_total"]
        assert "saludo.py" in final["files_written"]
