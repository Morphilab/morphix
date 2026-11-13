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


@pytest.fixture(autouse=True)
def _perfiles_de_agentes_disponibles(monkeypatch):
    """El runtime gatea los agentes contra agents_registry (no registrado →
    EngineError loud): estos tests ejercitan el CABLEADO dispatch, no la
    existencia de perfiles — el gate tiene sus tests en test_loop_rescates."""
    import agents.registry as reg

    monkeypatch.setattr(reg.agents_registry, "get_profile", lambda name: object())


@pytest.fixture(autouse=True)
def _journal_no_escribe_en_memoria_real(monkeypatch):
    """El journal es best-effort y tiene su test con tmp_path; los e2e del
    dispatch no deben ensuciar `memory/<ws>/runs/`."""
    from orchestration.dsl.run_journal import RunJournal

    monkeypatch.setattr(RunJournal, "write", lambda self, state, card: None)


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
    async def test_dsl_pausa_sin_conversacion_y_creacion_fallida_falla_claro(self, fake_ws):
        """Sin conversación Y con su creación fallando (BD caída), la pausa
        falla con mensaje claro — el único camino headless que queda: la
        conversación nace en la propia pausa (misma vía que el finalizer)."""
        doc = _dsl_doc([{"id": "humano", "kind": "checkpoint", "question": "¿Seguimos?"}])
        session = _session(conv_id=None)
        with (
            patch(
                "orchestration.workflows.orchestrator.load_workflow_document",
                return_value=doc,
            ),
            patch(
                "core.repositories.conversation_repository.ConversationRepository.save",
                new_callable=AsyncMock,
                side_effect=RuntimeError("bd no disponible"),
            ),
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
            id=101,
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

            async def get(self, model, pk):
                return paused_row

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
    async def test_el_cierre_es_el_ultimo_stats_pese_a_emits_diferidos(self, fake_ws):
        """El terminal debe llegar DESPUÉS de los emits de steps.

        El motor programa los stats de cada step como task; con un finalize
        que cede el loop (DB real) la task pendiente llegaba después del
        cierre y la GUI volvía a "Ejecutando (DSL)" con conteos viejos.
        """
        import asyncio as _asyncio

        doc = _dsl_doc([{"id": "paso1", "kind": "agent", "agent": "developer", "goal": "haz"}])
        session = _session(conv_id=5)

        async def _finalize_que_cede(**kwargs):
            await _asyncio.sleep(0)

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
                side_effect=_finalize_que_cede,
            ),
        ):
            await WorkflowOrchestrator.run_full_workflow(session, persist=True)

        stats = [c.args[0] for c in session.events.on_stats_update.await_args_list]
        assert stats, "sin emits de stats"
        assert stats[-1]["status"].startswith("Completado"), [s.get("status") for s in stats]
        # el cierre lleva el plan FINAL del motor (no el snapshot del último
        # step emitido): la GUI pinta el plan del reporte.
        assert stats[-1]["run_state"]["plan"], "el cierre no lleva el run_state final"
        assert "saludo.py" in stats[-1]["files_written"]

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
        assert sc["pasos"]["total"] >= 1
        assert sc["completadas"] == sc["subtasks"]
        # 3) emit TERMINAL con status Completado (la GUI apaga el chip ●)
        stats = [c.args[0] for c in session.events.on_stats_update.await_args_list]
        finals = [s for s in stats if str(s.get("status", "")).startswith("Completado")]
        assert finals, f"sin emit terminal; statuses: {[s.get('status') for s in stats]}"
        final = finals[-1]
        assert final["subtasks_total"] >= 1
        assert final["subtasks_completed"] == final["subtasks_total"]
        assert "saludo.py" in final["files_written"]


@pytest.mark.asyncio
async def test_run_stopped_persiste_calidad_fallido(fake_ws):
    from orchestration.dsl.engine import EngineResult

    doc = _dsl_doc([{"id": "paso1", "kind": "agent", "agent": "developer", "goal": "haz"}])
    session = _session()
    run_state = {
        "run_id": "r",
        "goal": "q",
        "plan": [
            {
                "kind": "subtask",
                "id": "1",
                "goal": "a",
                "path": "l#1",
                "status": "pending",
                "attempts": 0,
                "tokens": 0,
                "iterations": 0,
                "files_written": [],
                "verification": None,
                "satisfied_by": None,
                "notes": [],
                "last_error": None,
            }
        ],
        "budget": {"limit": 100, "spent": 0, "reserve_final": 0, "reserve_replan": 0},
        "replans": [],
        "memory": {},
    }
    stopped = EngineResult(
        status="stopped",
        stop_reason="CONTRADICCION: HU-A vs HU-B",
        run_state=run_state,
    )
    with (
        patch(
            "orchestration.workflows.orchestrator.load_workflow_document",
            return_value=doc,
        ),
        patch(
            "orchestration.dsl.engine.WorkflowEngine.run",
            new_callable=AsyncMock,
            return_value=stopped,
        ),
        patch(
            "orchestration.workflows.orchestrator.finalize_workflow",
            new_callable=AsyncMock,
        ) as mock_finalize,
        patch(
            "orchestration.workflows.orchestrator.RunJournal.write",
            return_value=None,
        ),
    ):
        out = await WorkflowOrchestrator.run_full_workflow(session, persist=True)
    assert "detenido" in out.lower()
    kw = mock_finalize.call_args.kwargs
    assert kw["status"] == "stopped"
    assert kw["scorecard"]["calidad"] == "Fallido"
    assert kw["scorecard"]["calidad_entrega"] == "Fallido"
    assert kw["scorecard"]["calidad_proceso"] == "Alta"


def _run_state(n):
    from orchestration.dsl.run_state import BudgetState, PlanItem, RunMemory, RunState

    return RunState(
        run_id="r",
        goal="g",
        plan=[
            PlanItem(kind="subtask", id=str(i), goal=str(i), path=f"l#{i}", status="delivered")
            for i in range(n)
        ],
        budget=BudgetState(1000, 0, 0, 0),
        replans=[],
        memory=RunMemory(),
    )


def test_dsl_scorecard_degradadas_bajan_la_calidad():
    """Un run con steps completados pero cortados por límite de iteraciones
    no puede reportar calidad 'Alta'."""
    from orchestration.workflows.orchestrator import _dsl_scorecard

    limpio = _dsl_scorecard(_run_state(9), tokens=1000, elapsed_s=12.5)
    assert limpio["calidad"] == "Alta"
    assert limpio["degradadas"] == 0

    degradado = _dsl_scorecard(_run_state(9), tokens=1000, elapsed_s=12.5, degradadas=4)
    assert degradado["calidad"] == "Parcial"
    assert degradado["degradadas"] == 4


def test_dsl_scorecard_sin_archivos_en_preset_con_proyecto():
    """Un flujo que exige proyecto y no entrega NINGÚN archivo no puede
    graduarse 'Alta' (un 'Alta' con 0 archivos es un no-op graduado)."""
    from orchestration.workflows.orchestrator import _dsl_scorecard

    con_entrega = _dsl_scorecard(
        _run_state(9), tokens=1000, elapsed_s=12.5, requiere_archivos=True, archivos_escritos=5
    )
    assert con_entrega["calidad"] == "Alta"
    assert "motivo" not in con_entrega

    sin_entrega = _dsl_scorecard(
        _run_state(9), tokens=1000, elapsed_s=12.5, requiere_archivos=True, archivos_escritos=0
    )
    assert sin_entrega["calidad"] == "Parcial"
    assert sin_entrega["motivo"] == "sin archivos entregados"

    # sin requirement de proyecto, 0 archivos no degrada (debate puro)
    debate = _dsl_scorecard(
        _run_state(8), tokens=1000, elapsed_s=12.5, requiere_archivos=False, archivos_escritos=0
    )
    assert debate["calidad"] == "Alta"


@pytest.mark.asyncio
async def test_run_dsl_fallido_deja_huella(monkeypatch, caplog):
    """Un run que falla a mitad del DSL NO puede esfumarse: log + workflow
    fila 'failed' + conversación persistida (un edd murió sin rastro)."""
    import logging
    from unittest.mock import AsyncMock, MagicMock

    import orchestration.loop as loop_mod
    from orchestration.context import WorkflowContext, WorkflowEvents
    from orchestration.dsl.compiler import compile_workflow
    from orchestration.dsl.runtime_adapter import ProductionRuntime
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    async def fake_loop(**kwargs):
        raise RuntimeError("contrato roto")

    monkeypatch.setattr(loop_mod, "execute_agent_loop", fake_loop)

    captured: dict = {}

    async def fake_finalize(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("orchestration.workflows.orchestrator.finalize_workflow", fake_finalize)
    monkeypatch.setattr(
        "orchestration.workflows.orchestrator.get_global_workspaces",
        MagicMock(current="main"),
    )

    doc = {
        "version": 1,
        "name": "demo",
        "agents": {"allowed": ["developer"]},
        "tools": {"allowed": []},
        "project": {"required": False},
        "steps": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "g"}],
    }
    cw = compile_workflow(doc)
    runtime = ProductionRuntime(workspace="main", tools_allowed=[], query="q")
    events = WorkflowEvents(
        on_stream_chunk=AsyncMock(),
        on_system_message=AsyncMock(),
        on_assistant_message=AsyncMock(),
        on_stats_update=AsyncMock(),
    )
    ctx = WorkflowContext(
        query="q",
        mode="orchestrate",
        conversation_history=[],
        workspace="main",
        project_root=None,
        active_workflow="demo",
        agents_registry=MagicMock(),
        enc=MagicMock(),
        allowed_tools=None,
    )
    with caplog.at_level(logging.ERROR, logger="orchestration.workflows.orchestrator"):
        msg = await WorkflowOrchestrator._finish_dsl_run(
            cw=cw,
            runtime=runtime,
            query="q",
            ctx=ctx,
            events=events,
            emitter=AsyncMock(),
            start_time=0.0,
            workflow_name="demo",
            resume=None,
            answer=None,
            persist=True,
        )

    assert "falló" in msg and "contrato roto" in msg
    assert "run DSL 'demo' falló" in caplog.text
    assert captured["status"] == "failed"
    assert captured["scorecard"]["calidad"] == "Fallido"


def test_append_quality_warning_alta_no_cambia():
    """Un run Alta no añade ruido al mensaje final."""
    from orchestration.workflows.orchestrator import _append_quality_warning

    out = _append_quality_warning("Tarea completada", {"calidad": "Alta", "degradadas": 0})
    assert out == "Tarea completada"


def test_append_quality_warning_parcial_dice_la_verdad():
    """El agregador puede decir success; el scorecard manda en el chat."""
    from orchestration.workflows.orchestrator import _append_quality_warning

    out = _append_quality_warning(
        "Tarea completada",
        {"calidad": "Parcial", "degradadas": 3, "recuperadas": 1},
        ["tool:test_runner", "ejecutar_subtareas ▸ 4 ▸ subtarea"],
    )
    assert out.startswith("⚠️ **Calidad: Parcial**")
    assert "Tarea completada" in out
    assert "Calidad: Parcial" in out
    assert "3 paso(s) degradado(s)" in out
    assert "1 recuperado(s)" in out
    assert "tool:test_runner" in out


def test_quality_warning_sobrevive_al_truncado_de_8000():
    """La advertencia va al inicio: el repositorio recorta a 8.000 chars."""
    from orchestration.workflows.orchestrator import _append_quality_warning

    out = _append_quality_warning("x" * 20000, {"calidad": "Parcial", "degradadas": 2})
    assert out.startswith("⚠️ **Calidad: Parcial**")
    assert "Calidad: Parcial" in out[:8000]
    assert "x" * 20000 in out


@pytest.mark.asyncio
async def test_run_parcial_abre_el_mensaje_con_el_reporte(fake_ws):
    """Criterio de cierre: el chat dice la calidad REAL, no 'completo'."""
    doc = _dsl_doc(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 1,
                "body": [
                    {
                        "id": "a",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "$item",
                        "produces": ["x.py"],
                    }
                ],
            }
        ],
        inputs=["items"],
        defaults={"items": ["a"]},
    )
    session = _session()
    with (
        patch(
            "orchestration.workflows.orchestrator.load_workflow_document",
            return_value=doc,
        ),
        patch(
            "orchestration.loop.execute_agent_loop",
            new_callable=AsyncMock,
            return_value={"status": "completed", "result": "LISTO", "files_written": []},
        ),
        patch(
            "orchestration.workflows.orchestrator.finalize_workflow",
            new_callable=AsyncMock,
        ) as mock_finalize,
        patch(
            "orchestration.workflows.orchestrator.RunJournal.write",
            return_value=None,
        ),
    ):
        out = await WorkflowOrchestrator.run_full_workflow(session, persist=True)

    assert out.startswith("⚠️ **Calidad: Parcial**")
    assert "entrega 0/1" in out
    sc = mock_finalize.call_args.kwargs["scorecard"]
    assert sc["entregadas"] == 0
    assert sc["sin_medicion"] == 1
