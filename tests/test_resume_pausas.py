# tests/test_resume_pausas.py — ciclo de vida de pausas/resume de workflows DSL
"""Contratos del resume de pausas: el resume corre CON el flag de ejecución
(⏹ visible, stats vivos, guard de doble envío activo), el recovery restaura
el project_root de la pausa, la primera corrida que pausa no pierde el run,
y un doc editado durante la pausa se re-valida y se reporta."""

import asyncio
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402


def _qapp():
    from typing import cast

    from PySide6.QtWidgets import QApplication

    return cast(QApplication, QApplication.instance() or QApplication([]))


def test_resume_workflow_toma_el_flag_de_running():
    """Durante el resume el pane ESTÁ en ejecución: el ⏹ vive (su visibilidad
    depende exclusivamente del flag), los stats no se descartan y el guard de
    doble envío aplica — y el finally lo limpia al terminar."""
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._set_workflow_running(False)
    visto = []

    class _Runner:
        async def resume(self, session, answer):
            visto.append(m._workflow_running)

    m._runner = _Runner()
    session = SimpleNamespace(
        context=SimpleNamespace(project_root=None, conversation_id=None, conversation_history=[])
    )

    asyncio.run(m._resume_workflow(session, "sí, sigue"))

    assert visto == [True], "el resume debe correr con el flag de running en True"
    assert m._workflow_running is False, "el finally debe limpiar el flag"


# ── Task 2: el recovery no pierde project_root ─────────────────────────────


def test_resolve_resume_project_root_precedencia():
    """Precedencia del project_root en el resume: ctx (el recovery lo restaura
    desde la pausa) → root del doc expandido → None. El fresh usa doc→ctx; el
    resume invierte: la pausa capturó la resolución ORIGINAL del run."""
    from orchestration.workflows.orchestrator import _resolve_resume_project_root

    cw = SimpleNamespace(dsl=SimpleNamespace(project=SimpleNamespace(root=None)))
    ctx = SimpleNamespace(project_root="code_projects/original")

    assert _resolve_resume_project_root(cw, ctx, {}) == "code_projects/original"

    ctx_legacy = SimpleNamespace(project_root=None)
    cw_doc = SimpleNamespace(dsl=SimpleNamespace(project=SimpleNamespace(root="/tmp/fijo")))
    assert _resolve_resume_project_root(cw_doc, ctx_legacy, {}) == "/tmp/fijo"

    assert _resolve_resume_project_root(cw, ctx_legacy, {}) is None


def test_rearm_paused_session_restaura_project_root():
    """El recovery reconstruye el ctx con el project_root persistido en la
    pausa — sin él, la política projectless dropeaba TODAS las tools de FS
    del run reanudado tras cerrar la pane o reiniciar."""
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    paused_state = {
        "origin": "dsl",
        "workflow": "tdd",
        "query": "tarea",
        "project_root": "code_projects/demo",
        "paused_step": "cp",
        "paused_kind": "checkpoint",
    }
    m._rearm_paused_session(7, {"question": "¿seguimos?", "paused_state": json.dumps(paused_state)})
    assert m._paused_session is not None
    assert m._paused_session.context.project_root == "code_projects/demo"


def test_finish_dsl_run_pausa_persiste_project_root(monkeypatch):
    """La pausa viaja con el project_root del run: el recovery lo necesita para
    que el resume no corra con las tools de FS dropeadas."""
    import asyncio

    from orchestration.dsl.engine import EngineResult
    from orchestration.workflows import orchestrator as orch

    capturada = {}

    async def _fake_save(conv_id, query, question, options, paused_state):
        capturada.update(paused_state)

    monkeypatch.setattr(orch.pauses, "save_paused_session", _fake_save)

    class _EnginePausado:
        def __init__(self, runtime):
            pass

        async def run(self, cw, inputs=None, resume=None, default_budget=0):
            return EngineResult(
                status="paused",
                snapshot={"vars": {}, "completed": [], "loop_state": {}, "children": {}},
                paused_question="¿seguimos?",
                paused_step_id="cp",
                paused_kind="checkpoint",
            )

    monkeypatch.setattr("orchestration.dsl.engine.WorkflowEngine", _EnginePausado)

    cw = SimpleNamespace(dsl=SimpleNamespace(outputs=[], tools=SimpleNamespace(allowed=[])))
    ctx = SimpleNamespace(
        conversation_id=7,
        project_root="code_projects/demo",
        conversation_history=[],
    )
    events = SimpleNamespace(on_system_message=None, on_stream_chunk=None)

    out = asyncio.run(
        orch.WorkflowOrchestrator._finish_dsl_run(
            cw=cw,
            runtime=SimpleNamespace(),
            query="tarea",
            ctx=ctx,
            events=events,
            emitter=SimpleNamespace(emit=AsyncMock()),
            start_time=0.0,
            workflow_name="tdd",
            resume=None,
            answer=None,
            persist=True,
        )
    )
    assert out == orch.PAUSED_MARKER
    assert capturada.get("project_root") == "code_projects/demo"


# ── Pausa en primera corrida: el run no se pierde ──────────────────────────


def test_pausa_en_primera_corrida_crea_la_conversacion(monkeypatch):
    """La conversación nace en el finalize — que jamás corre en una pausa.
    Una pausa en la PRIMERA corrida de una pane (conv_id None) debe crear
    la conversación ANTES de persistir la pausa: sin ella, el run completo
    se pierde (sin conversación, sin snapshot, sin fila Workflow) y el
    mensaje headless se pinta como respuesta del asistente."""
    from orchestration.dsl.engine import EngineResult
    from orchestration.workflows import orchestrator as orch

    capturada = {}

    async def _fake_save(conv_id, query, question, options, paused_state):
        capturada["conv_id"] = conv_id

    monkeypatch.setattr(orch.pauses, "save_paused_session", _fake_save)
    monkeypatch.setattr(
        "core.repositories.conversation_repository.ConversationRepository.save",
        AsyncMock(return_value=42),
    )

    class _EnginePausado:
        def __init__(self, runtime):
            pass

        async def run(self, cw, inputs=None, resume=None, default_budget=0):
            return EngineResult(
                status="paused",
                snapshot={"vars": {}, "completed": [], "loop_state": {}, "children": {}},
                paused_question="¿seguimos?",
                paused_step_id="cp",
                paused_kind="checkpoint",
            )

    monkeypatch.setattr("orchestration.dsl.engine.WorkflowEngine", _EnginePausado)

    cw = SimpleNamespace(dsl=SimpleNamespace(outputs=[], tools=SimpleNamespace(allowed=[])))
    ctx = SimpleNamespace(conversation_id=None, project_root=None, conversation_history=[])
    events = SimpleNamespace(on_system_message=None, on_stream_chunk=None)

    out = asyncio.run(
        orch.WorkflowOrchestrator._finish_dsl_run(
            cw=cw,
            runtime=SimpleNamespace(),
            query="tarea",
            ctx=ctx,
            events=events,
            emitter=SimpleNamespace(emit=AsyncMock()),
            start_time=0.0,
            workflow_name="tdd",
            resume=None,
            answer=None,
            persist=True,
        )
    )
    assert out == orch.PAUSED_MARKER, "la pausa de primera corrida no es headless: persiste"
    assert ctx.conversation_id == 42, "el ctx adopta la conversación creada"
    assert capturada.get("conv_id") == 42, "la pausa se persiste contra la conversación nueva"


def test_after_workflow_prefiere_ctx_conversation_id(monkeypatch):
    """Los mensajes agent/tool del tramo previo a una pausa de primera
    corrida también se persisten: _after_workflow debe adoptar el
    conversation_id del ctx (la pausa lo setea al crear la conversación)
    antes que el ContextVar del finalizer (que no corrió)."""
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._conversation_id = None
    m._history = [
        {"role": "user", "content": "tarea"},
        {"role": "agent", "content": "paso1"},
    ]
    session = SimpleNamespace(
        context=SimpleNamespace(
            project_root=None,
            conversation_id=42,
            conversation_history=[{"role": "user", "content": "tarea"}],
        )
    )
    add_calls = AsyncMock()
    monkeypatch.setattr(
        "core.repositories.conversation_repository.ConversationRepository.add_messages",
        add_calls,
    )
    asyncio.run(m._after_workflow(session))
    assert m._conversation_id == 42
    add_calls.assert_awaited_once()
    msgs = add_calls.await_args.args[1]
    assert msgs == [{"role": "agent", "content": "paso1"}]


# ── Resume con doc editado: honesto ────────────────────────────────────────

_DOC_VALIDO = {
    "version": 1,
    "name": "demo",
    "steps": [{"id": "p", "kind": "agent", "agent": "developer", "goal": "x"}],
    "agents": {"allowed": ["developer"]},
    "tools": {"allowed": []},
}


def _ses_min(paused_state_extra: dict) -> tuple:
    paused_data = {
        "origin": "dsl",
        "workflow": "demo",
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
    }
    paused_data.update(paused_state_extra)
    session = SimpleNamespace(
        context=SimpleNamespace(
            workspace="main",
            conversation_history=[],
            query="tarea",
            project_root=None,
            conversation_id=7,
        ),
        events=SimpleNamespace(on_system_message=None, on_stream_chunk=None),
        emitter=None,
    )
    return session, paused_data


def test_resume_doc_editado_que_no_valida_falla_claro(monkeypatch):
    """Editar el doc durante la pausa y dejarlo semánticamente roto debe
    fallar ANTES de correr el motor (paridad con el fresh, que valida) —
    antes el resume corría cualquier cosa sin el gate."""
    import asyncio

    from orchestration.workflows import orchestrator as orch

    doc_roto = {
        "version": 1,
        "name": "demo",
        "steps": [{"id": "p", "kind": "agent", "agent": "fantasma", "goal": "x"}],
        "agents": {"allowed": ["developer"]},
        "tools": {"allowed": []},
    }
    monkeypatch.setattr(orch, "load_workflow_document", lambda ws, name: doc_roto)
    finish = AsyncMock(return_value="CORRIO")
    monkeypatch.setattr(orch.WorkflowOrchestrator, "_finish_dsl_run", finish)

    session, paused_data = _ses_min({})
    out = asyncio.run(
        orch.WorkflowOrchestrator._resume_dsl(
            session, paused_data=paused_data, question="¿ok?", answer="sí", start_time=0.0
        )
    )
    assert "semánticos" in out, "la edición rompió la validación y debe decirlo"
    assert finish.await_count == 0, "con errores semánticos el motor NO corre"


def test_resume_doc_editado_avisa_y_procede(monkeypatch):
    """Huella del doc en la pausa: un doc editado (hash distinto) avisa en
    el chat ANTES de proseguir — los steps renombrados re-ejecutan y los
    ítems del loop pueden desalinearse; el usuario merece saberlo. Con el
    doc intacto, ningún aviso."""
    import asyncio

    from orchestration.workflows import orchestrator as orch

    mensajes: list[str] = []

    async def _emit(events, msg):
        mensajes.append(msg)

    monkeypatch.setattr(orch, "load_workflow_document", lambda ws, name: dict(_DOC_VALIDO))
    monkeypatch.setattr(orch, "emit_system", _emit)
    finish = AsyncMock(return_value="CORRIO")
    monkeypatch.setattr(orch.WorkflowOrchestrator, "_finish_dsl_run", finish)

    hash_distinto = "0" * 64
    session, paused_data = _ses_min({"doc_sha256": hash_distinto})
    out = asyncio.run(
        orch.WorkflowOrchestrator._resume_dsl(
            session, paused_data=paused_data, question="¿ok?", answer="sí", start_time=0.0
        )
    )
    assert out == "CORRIO", "la edición avisa pero PROCEDE"
    assert any("editado desde la pausa" in m for m in mensajes)
    assert finish.await_count == 1

    # mismo doc: sin aviso
    mensajes.clear()
    finish.reset_mock()
    session, paused_data = _ses_min({"doc_sha256": orch._doc_sha256(dict(_DOC_VALIDO))})
    out = asyncio.run(
        orch.WorkflowOrchestrator._resume_dsl(
            session, paused_data=paused_data, question="¿ok?", answer="sí", start_time=0.0
        )
    )
    assert out == "CORRIO"
    assert not any("editado desde la pausa" in m for m in mensajes)


def test_finish_dsl_run_pausa_persiste_doc_sha256(monkeypatch):
    """La huella viaja en la pausa para que el resume pueda compararla."""
    import asyncio

    from orchestration.dsl.engine import EngineResult
    from orchestration.workflows import orchestrator as orch

    capturada = {}

    async def _fake_save(conv_id, query, question, options, paused_state):
        capturada.update(paused_state)

    monkeypatch.setattr(orch.pauses, "save_paused_session", _fake_save)

    class _EnginePausado:
        def __init__(self, runtime):
            pass

        async def run(self, cw, inputs=None, resume=None, default_budget=0):
            return EngineResult(
                status="paused",
                snapshot={"vars": {}, "completed": [], "loop_state": {}, "children": {}},
                paused_question="¿seguimos?",
                paused_step_id="cp",
                paused_kind="checkpoint",
            )

    monkeypatch.setattr("orchestration.dsl.engine.WorkflowEngine", _EnginePausado)
    monkeypatch.setattr(
        "core.repositories.conversation_repository.ConversationRepository.save",
        AsyncMock(return_value=42),
    )

    cw = SimpleNamespace(dsl=SimpleNamespace(outputs=[], tools=SimpleNamespace(allowed=[])))
    ctx = SimpleNamespace(conversation_id=None, project_root=None, conversation_history=[])
    events = SimpleNamespace(on_system_message=None, on_stream_chunk=None)

    out = asyncio.run(
        orch.WorkflowOrchestrator._finish_dsl_run(
            cw=cw,
            runtime=SimpleNamespace(),
            query="tarea",
            ctx=ctx,
            events=events,
            emitter=SimpleNamespace(emit=AsyncMock()),
            start_time=0.0,
            workflow_name="tdd",
            resume=None,
            answer=None,
            persist=True,
            doc_sha256="abc123",
        )
    )
    assert out == orch.PAUSED_MARKER
    assert capturada.get("doc_sha256") == "abc123"


# ── El scorecard no inventa tokens ─────────────────────────────────────────


def test_scorecard_sin_llm_reporta_cero_tokens(monkeypatch):
    """Un run sin llamadas LLM reporta 0 tokens — el fallback len(msg)//4
    inventaba una medición del largo del texto final, contra la doctrina
    'sin medición no es medir cero' aplicada a los conteos de test_runner."""
    import asyncio

    from orchestration.dsl.engine import EngineResult
    from orchestration.workflows import orchestrator as orch

    class _EngineFallido:
        def __init__(self, runtime):
            pass

        async def run(self, cw, inputs=None, resume=None, default_budget=0):
            return EngineResult(status="failed", failure="algo explotó")

    monkeypatch.setattr("orchestration.dsl.engine.WorkflowEngine", _EngineFallido)
    monkeypatch.setattr("tools.orchestrator.get_llm_token_usage", lambda: 0)
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "finalize_workflow", finalize)

    cw = SimpleNamespace(dsl=SimpleNamespace(outputs=[], tools=SimpleNamespace(allowed=[])))
    ctx = SimpleNamespace(conversation_id=7, project_root=None, conversation_history=[])
    events = SimpleNamespace(on_system_message=None, on_stream_chunk=None)
    runtime = SimpleNamespace(
        _subtasks={}, subtask_summary=(0, 0, []), degraded_steps=[], files_written=[]
    )

    out = asyncio.run(
        orch.WorkflowOrchestrator._finish_dsl_run(
            cw=cw,
            runtime=runtime,
            query="tarea",
            ctx=ctx,
            events=events,
            emitter=SimpleNamespace(emit=AsyncMock()),
            start_time=0.0,
            workflow_name="tdd",
            resume=None,
            answer=None,
            persist=True,
        )
    )
    assert "falló" in out
    scorecard = finalize.await_args.kwargs["scorecard"]
    assert scorecard["tokens"] == 0, "sin llamadas LLM el scorecard reporta cero, no un invento"
