"""Contrato normalizado de stats — WorkflowEmitter.

Garantías del contrato (spec §3):
- Todos los campos siempre presentes en el dict emitido.
- elapsed_time y tokens_used medidos automáticamente (nunca "—").
- files_written siempre lista.
- subtask_list con default [consulta] cuando el workflow no provee pasos.
- Campos desconocidos → TypeError (validación interna).
"""

from unittest.mock import AsyncMock

import pytest

from orchestration.context import Session, WorkflowContext
from orchestration.emitter import WorkflowEmitter

ALL_FIELDS = [
    "status",
    "current_agent",
    "subtask_list",
    "subtasks_total",
    "subtasks_completed",
    "files_written",
    "tokens_used",
    "elapsed_time",
    "phase",
    "iterations",
    "actions_taken",
]


def _make_session() -> tuple[Session, AsyncMock]:
    events = AsyncMock()
    ctx = WorkflowContext(query="haz una api de tareas", mode="orchestrate")
    return Session(context=ctx, events=events), events


@pytest.mark.asyncio
async def test_emit_always_has_all_contract_fields():
    session, events = _make_session()
    emitter = WorkflowEmitter(session)
    await emitter.emit(status="Iniciando", current_agent="TaskAnalyzer")

    assert events.on_stats_update.await_count == 1
    data: dict = events.on_stats_update.await_args.args[0]
    for field in ALL_FIELDS:
        assert field in data, f"campo faltante: {field}"


@pytest.mark.asyncio
async def test_emit_measures_elapsed_and_tokens():
    session, events = _make_session()
    emitter = WorkflowEmitter(session)
    await emitter.emit(status="Completado")

    data: dict = events.on_stats_update.await_args.args[0]
    assert isinstance(data["elapsed_time"], str) and data["elapsed_time"].endswith("s")
    assert isinstance(data["tokens_used"], int)


@pytest.mark.asyncio
async def test_default_subtask_list_is_the_query():
    session, events = _make_session()
    emitter = WorkflowEmitter(session)
    await emitter.emit(status="Completado")

    data: dict = events.on_stats_update.await_args.args[0]
    assert data["subtask_list"] == [{"name": "haz una api de tareas", "status": "pending"}]


@pytest.mark.asyncio
async def test_files_written_always_list():
    session, events = _make_session()
    emitter = WorkflowEmitter(session)
    await emitter.emit(files_written=["src/app.py"])

    data: dict = events.on_stats_update.await_args.args[0]
    assert isinstance(data["files_written"], list)
    assert data["files_written"] == ["src/app.py"]


@pytest.mark.asyncio
async def test_unknown_field_raises_typeerror():
    session, _ = _make_session()
    emitter = WorkflowEmitter(session)
    with pytest.raises(TypeError, match="Campo desconocido"):
        await emitter.emit(total_tools=99)


@pytest.mark.asyncio
async def test_emit_failure_does_not_raise():
    """Un fallo de emisión nunca rompe el workflow (spec §7)."""
    session, events = _make_session()
    events.on_stats_update.side_effect = RuntimeError("UI cerrando")
    emitter = WorkflowEmitter(session)
    # No debe propagar la excepción
    await emitter.emit(status="Completado")


@pytest.mark.asyncio
async def test_subsequent_emits_accumulate_state():
    session, events = _make_session()
    emitter = WorkflowEmitter(session)
    await emitter.emit(status="Ejecutando", subtasks_total=3, subtasks_completed=1)
    await emitter.emit(subtasks_completed=2)

    assert events.on_stats_update.await_count == 2
    data: dict = events.on_stats_update.await_args.args[0]
    assert data["subtasks_total"] == 3
    assert data["subtasks_completed"] == 2
    assert data["status"] == "Ejecutando"


@pytest.mark.asyncio
async def test_phase_none_resets_phase():
    """phase=None limpia la fase anterior (los emits finales no dejan fase stale)."""
    session, events = _make_session()
    emitter = WorkflowEmitter(session)
    await emitter.emit(status="Sintetizando", phase="Sintetizando")
    await emitter.emit(status="Completado", phase=None)

    assert events.on_stats_update.await_count == 2
    data: dict = events.on_stats_update.await_args.args[0]
    assert data["phase"] is None
