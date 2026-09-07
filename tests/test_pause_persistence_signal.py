# tests/test_pause_persistence_signal.py
"""La pérdida de una pausa (fallo de BD al persistir PausedSession) no debe
ser silenciosa: el usuario respondería la clarificación a un flujo que jamás
se reanudaría. La señal es loud (evento de sistema) en AMBAS rutas de pausa
(bot canónico y DSL), que comparten el helper de orchestration/pauses.py."""

import inspect
from unittest.mock import AsyncMock

import orchestration.workflows.orchestrator as orch
from orchestration import bots_dispatch, pauses
from orchestration.context import WorkflowEvents


async def test_warn_pause_not_persisted_emite_sena_visible():
    emit_mock = AsyncMock()
    original = pauses.emit_system
    pauses.emit_system = emit_mock
    try:
        await pauses.warn_pause_not_persisted(
            WorkflowEvents(), "dsl", RuntimeError("conexión BD caída")
        )
    finally:
        pauses.emit_system = original

    assert emit_mock.await_count == 1
    args = emit_mock.await_args.args
    assert isinstance(args[0], WorkflowEvents)
    msg = args[1]
    assert "⚠️" in msg and "pausa (dsl)" in msg
    assert "conexión BD caída" in msg
    assert "NO se reanudará" in msg


def test_ambas_rutas_de_pausa_usan_la_sena_loud():
    """Guard estructural: las DOS rutas de pausa (bot canónico en
    bots_dispatch, DSL en el orquestador) delegan en el helper compartido —
    si alguien añade una tercera ruta de pausa, debe hacer lo mismo."""
    src_orch = inspect.getsource(orch)
    src_bots = inspect.getsource(bots_dispatch)

    assert src_orch.count("warn_pause_not_persisted(") >= 1, "la ruta DSL debe emitir la señal loud"
    assert (
        src_bots.count("warn_pause_not_persisted(") >= 1
    ), "la ruta bot canónica debe emitir la señal loud"
    assert hasattr(pauses, "save_paused_session") and hasattr(pauses, "warn_pause_not_persisted")
