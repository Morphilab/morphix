"""Switch de workspace denegado mientras hay workflows activos.

Deny-by-default en backend: `begin_workflow_run()`/`end_workflow_run()` rodean
`run_full_workflow`; `switch_workspace()` se niega mientras el contador global
de runs esté >0 (salvo `force=True`). El veto del guard de la GUI queda como
capa adicional e independiente.

Nota: el contador es in-memory — tras un restart del proceso vale 0, por eso
`resume_workflow` NO toma token (es continuación, no un run nuevo).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.workspaces as ws_mod
from orchestration.context import Session, WorkflowContext, WorkflowEvents


@pytest.fixture(autouse=True)
def _reset_active_runs():
    """Aísla el contador global in-memory entre tests."""
    with ws_mod._run_lock:
        ws_mod._active_runs = 0
    yield
    with ws_mod._run_lock:
        ws_mod._active_runs = 0


def _make_ctx(query: str = "Hola", workspace: str = "main") -> WorkflowContext:
    return WorkflowContext(
        query=query,
        mode="chat",
        conversation_history=[],
        workspace=workspace,
        project_root=None,
        current_pdf_text=None,
        active_workflow=None,
        settings=MagicMock(),
        agents_registry=MagicMock(),
        enc=MagicMock(),
        allowed_tools=None,
    )


def _make_events() -> WorkflowEvents:
    return WorkflowEvents(
        on_stream_chunk=AsyncMock(),
        on_system_message=AsyncMock(),
        on_assistant_message=AsyncMock(),
        on_stats_update=AsyncMock(),
        on_ui_refresh=AsyncMock(),
    )


# ── Contador de runs activos ──


def test_counter_lifecycle_and_floor_at_zero():
    """begin/end incrementan-decrementan; ends extra nunca negativizan."""
    from core.workspaces import begin_workflow_run, end_workflow_run, workflow_running

    assert workflow_running() is False
    t1 = begin_workflow_run()
    t2 = begin_workflow_run()
    assert t2 != t1
    assert workflow_running() is True

    end_workflow_run(t2)
    assert workflow_running() is True
    end_workflow_run(t1)
    assert workflow_running() is False

    end_workflow_run(t1)  # end extra tras llegar a 0
    assert workflow_running() is False


# ── Guard en switch_workspace ──


@pytest.mark.asyncio
async def test_switch_allowed_without_active_runs_then_denied_while_running():
    """Sin runs activos el switch pasa; con un run activo se deniega ANTES de
    tocar internals (_do_switch_workspace no se awaita)."""
    from core.workspaces import Workspaces, begin_workflow_run, end_workflow_run, workflow_running

    mgr = Workspaces()
    do_switch = AsyncMock(return_value=True)

    with patch.object(mgr, "_do_switch_workspace", do_switch):
        ok_before = await mgr.switch_workspace("destino")
        assert ok_before is True
        do_switch.assert_awaited_once()

        token = begin_workflow_run()
        try:
            assert workflow_running() is True
            do_switch.reset_mock()
            ok = await mgr.switch_workspace("otro")
            assert ok is False
            do_switch.assert_not_awaited(), "denegado sin tocar BD/loaders"
        finally:
            end_workflow_run(token)

    assert workflow_running() is False


@pytest.mark.asyncio
async def test_switch_allowed_with_force_while_running():
    """force=True bypassa el guard de workflows activos (escape explícito del operador)."""
    from core.workspaces import Workspaces, begin_workflow_run, end_workflow_run

    mgr = Workspaces()
    do_switch = AsyncMock(return_value=True)

    with patch.object(mgr, "_do_switch_workspace", do_switch):
        token = begin_workflow_run()
        try:
            ok = await mgr.switch_workspace("destino", force=True)
            assert ok is True
            do_switch.assert_awaited_once_with("destino", 1)
        finally:
            end_workflow_run(token)


@pytest.mark.asyncio
async def test_denial_precedes_gui_guard(caplog):
    """La denegación por workflows activos corta ANTES del guard de la GUI:
    ni siquiera lo consulta."""
    from core.workspaces import Workspaces, begin_workflow_run, end_workflow_run

    mgr = Workspaces()
    guard_calls: list[str] = []
    mgr.set_switch_guard(lambda name: guard_calls.append(name) or True)

    token = begin_workflow_run()
    try:
        with caplog.at_level("WARNING"):
            ok = await mgr.switch_workspace("destino")
        assert ok is False
        assert guard_calls == [], "el guard GUI no debe consultarse si H5b deniega"
        assert any("workflow en ejecución" in r.message for r in caplog.records)
    finally:
        end_workflow_run(token)


# ── El orquestador sostiene el token durante la ejecución ──


def _orchestrator_patches(dispatch_impl):
    """Parches mínimos para ejecutar run_full_workflow con ruta despachada."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    dispatch = AsyncMock(side_effect=dispatch_impl)
    return (
        patch(
            "core.security.undercover_mode.undercover.check_query",
            return_value=True,
        ),
        patch(
            "orchestration.workflows.orchestrator.get_global_workspaces",
            return_value=MagicMock(current="main"),
        ),
        patch.object(WorkflowOrchestrator, "_dispatch_route", dispatch),
    ), dispatch


@pytest.mark.asyncio
async def test_orchestrator_holds_token_during_execution():
    """Durante run_full_workflow (mockeado lento) workflow_running() es True;
    al terminar, False otra vez."""
    from core.workspaces import workflow_running
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    seen: dict = {}

    async def slow_dispatch(**kwargs):
        seen["during"] = workflow_running()
        await asyncio.sleep(0.01)
        return "hecho"

    patches, _ = _orchestrator_patches(slow_dispatch)
    with patches[0], patches[1], patches[2]:
        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=_make_ctx(), events=_make_events())
        )

    assert result == "hecho"
    assert seen["during"] is True, "el token debe estar tomado durante la ejecución"
    assert workflow_running() is False, "el token debe liberarse al terminar"


@pytest.mark.asyncio
async def test_orchestrator_releases_token_on_exception():
    """Si la ejecución explota, el finally libera el token igualmente."""
    from core.workspaces import workflow_running
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    async def boom(**kwargs):
        raise RuntimeError("fallo simulado")

    patches, _ = _orchestrator_patches(boom)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(RuntimeError, match="fallo simulado"):
            await WorkflowOrchestrator.run_full_workflow(
                session=Session(context=_make_ctx(), events=_make_events())
            )

    assert workflow_running() is False


@pytest.mark.asyncio
async def test_delete_workspace_denied_while_workflow_running(monkeypatch):
    """delete_workspace (current o no) consulta workflow_running() — con
    workflows activos NO toca la BD (drop_schema jamás se awaita)."""
    from core.workspaces import Workspaces, begin_workflow_run, end_workflow_run, workflow_running

    mgr = Workspaces()
    mgr.current = "otro"  # escenario non-current: el hueco documentado
    drop = AsyncMock(return_value=None)
    monkeypatch.setattr("core.workspaces.drop_schema", drop)

    token = begin_workflow_run()
    try:
        ok = await mgr.delete_workspace("victim")
        assert ok is False
        drop.assert_not_awaited()
    finally:
        end_workflow_run(token)

    assert workflow_running() is False
    # Sin runs: el delete procede (drop awaitado)
    ok2 = await mgr.delete_workspace("victim")
    assert ok2 is True
    drop.assert_awaited_once_with("victim")


@pytest.mark.asyncio
async def test_delete_current_still_escapes_first_then_drops(monkeypatch):
    """delete del workspace ACTUAL mantiene su escape-switch previo."""
    from core.workspaces import Workspaces

    mgr = Workspaces()
    mgr.current = "activo"
    monkeypatch.setattr("core.workspaces.drop_schema", AsyncMock(return_value=None))
    with patch.object(mgr, "switch_workspace", AsyncMock(return_value=True)) as sw:
        sw.side_effect = None

        async def _escape(name):
            mgr.current = name
            return True

        sw.side_effect = _escape
        ok = await mgr.delete_workspace("activo")
    assert ok is True
