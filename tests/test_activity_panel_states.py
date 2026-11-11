"""Chip de actividad + nota de sesión vacía del Maestro.

Verificación headless del MaestroTab: dot ●/○ con tooltip, nota dashed del
chat (idle_note) y detección de running desde el contrato real de stats_update.
"""

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from typing import cast  # noqa: E402

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from desktop.theme import ThemeManager  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def test_header_titulo_y_estado_inicial_idle():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()

    header = m._activity_dot.parentWidget()
    assert header is not None
    assert any(lbl.text() == "ACTIVIDAD" for lbl in header.findChildren(QLabel))

    assert m._activity_dot.text() == "○"
    assert m._activity_dot.toolTip() == "Sin ejecución activa"
    # Nota de sesión vacía (opción A): vive en el CHAT y solo en idle
    assert not m._idle_note.isHidden()


def test_set_activity_state_conmuta_dot_tooltip_y_empty():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()

    m._set_activity_state(True)
    assert m._activity_dot.text() == "●"
    assert m._activity_dot.toolTip() == "Workflow en ejecución"
    assert ThemeManager.current().colors.status_running in m._activity_dot.styleSheet()

    m._set_activity_state(False)
    assert m._activity_dot.text() == "○"
    assert m._activity_dot.toolTip() == "Sin ejecución activa"
    assert not m._idle_note.isHidden()


def test_idle_note_ciclo_con_contenido():
    """La nota se oculta con el primer contenido y reaparece al limpiar."""
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    assert not m._idle_note.isHidden()

    m._add_bubble("hola", "user")
    assert m._idle_note.isHidden()

    m.clear_chat(force=True)
    assert not m._idle_note.isHidden()


def test_on_stats_dirige_el_chip():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._set_workflow_running(True)  # los stats solo aplican con run vivo

    # Workflow vivo: subtareas en marcha
    m._on_stats({"status": "Ejecutando subtarea 1", "subtasks_total": 3})
    assert m._activity_dot.text() == "●"

    # Fases/chat directo sin descomposición también son actividad
    m._set_activity_state(False)
    m._on_stats({"status": "Respondiendo"})
    assert m._activity_dot.text() == "●"

    # Terminación normal → idle
    m._on_stats({"status": "Completado", "subtasks_total": 3})
    assert m._activity_dot.text() == "○"

    # Pausa de pipeline (gate/checkpoint) → idle
    m._on_stats({"status": "paused", "subtasks_total": 2})
    assert m._activity_dot.text() == "○"


def test_terminal_spanish_status_sets_idle():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._set_workflow_running(True)

    # "Fallido" (emit final de ruta TDD fallida) NO contiene "fail"
    m._on_stats({"status": "Fallido"})
    assert m._activity_dot.text() == "○"

    for terminal in ("Cancelado", "cancelled", "failed", "completed", "Error fatal"):
        m._set_activity_state(True)
        assert m._activity_dot.text() == "●"
        m._on_stats({"status": terminal})
        assert m._activity_dot.text() == "○", terminal


def test_aggregating_status_stays_running():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._set_workflow_running(True)
    # Contiene "failed" en medio pero es fase intermedia (ventana LLM larga)
    m._on_stats({"status": "Aggregating (2 done, 1 failed)"})
    assert m._activity_dot.text() == "●"


def test_round_completed_stays_running():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._set_workflow_running(True)
    # Parpadeo entre rondas collaborative: "completada" en medio, no al inicio
    m._on_stats({"status": "Ronda 2/3 completada"})
    assert m._activity_dot.text() == "●"
    m._on_stats({"status": "DAG level completed"})
    assert m._activity_dot.text() == "●"


def test_status_vacio_cae_a_subtasks_total():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._set_workflow_running(True)
    # Payload legacy sin status: solo subtareas cuentan como actividad
    m._set_activity_state(False)
    m._on_stats({"subtasks_total": 0})
    assert m._activity_dot.text() == "○"
    m._on_stats({"subtasks_total": 3})
    assert m._activity_dot.text() == "●"


def test_on_finish_fuerza_idle():
    # Ruta sin emit terminal (coordinated) o excepción: el callback del runner resetea
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._set_activity_state(True)
    assert m._activity_dot.text() == "●"
    m._runner.on_finish()
    assert m._activity_dot.text() == "○"


@pytest.mark.asyncio
async def test_runner_invoca_on_finish_al_terminar(monkeypatch):
    # El runner llama on_finish UNA vez por run, en éxito Y en excepción.
    from unittest.mock import MagicMock

    from desktop.services.workflow_runner import WorkflowRunner
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    runner = WorkflowRunner()
    calls: list[str] = []
    runner.on_finish = lambda: calls.append("finish")

    async def _boom(session=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(WorkflowOrchestrator, "run_full_workflow", _boom)
    await runner.run(MagicMock())  # excepción capturada dentro del runner
    assert calls == ["finish"]

    async def _ok(session=None):
        return "resultado final"

    monkeypatch.setattr(WorkflowOrchestrator, "run_full_workflow", _ok)
    await runner.run(MagicMock())
    assert calls == ["finish", "finish"]


@pytest.mark.asyncio
async def test_on_runner_pause_deja_idle():
    from unittest.mock import MagicMock

    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._set_activity_state(True)
    await m._on_runner_pause(MagicMock(), "¿Qué formato?")
    assert m._activity_dot.text() == "○"


# ── ⏹ detener persiste conversación parcial ──


@pytest.mark.asyncio
async def test_runner_captura_cancelled_y_persiste(monkeypatch):
    """⏹ = CancelledError: mensaje claro + on_cancel_persist + on_finish."""
    from unittest.mock import MagicMock

    from desktop.services.workflow_runner import WorkflowRunner
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    runner = WorkflowRunner()
    calls: list[str] = []
    persisted: list[str] = []
    runner.on_finish = lambda: calls.append("finish")

    async def _sys(msg):
        persisted.append(msg)

    runner.on_system = _sys
    runner.on_cancel_persist = _make_async_capture(persisted)

    async def _cancelled(session=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(WorkflowOrchestrator, "run_full_workflow", _cancelled)
    await runner.run(MagicMock())
    assert persisted, "sin mensaje de cancelación"
    assert any("⏹" in p or "Detenido" in str(p) for p in persisted)
    assert calls == ["finish"]


@pytest.mark.asyncio
async def test_runner_cancelled_sin_callback_persist_no_explota(monkeypatch):
    from unittest.mock import MagicMock

    from desktop.services.workflow_runner import WorkflowRunner
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    runner = WorkflowRunner()
    msgs: list[str] = []

    async def _sys(msg):
        msgs.append(msg)

    runner.on_system = _sys

    async def _cancelled(session=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(WorkflowOrchestrator, "run_full_workflow", _cancelled)
    await runner.run(MagicMock())  # sin on_cancel_persist → no debe reventar
    assert any("⏹" in m or "Detenido" in str(m) for m in msgs)


def _make_async_capture(sink: list):
    from unittest.mock import AsyncMock

    return AsyncMock(side_effect=lambda *a, **k: sink.append(a or k))


# ── banner de presupuesto de tokens ──


@pytest.fixture()
def _budget_fijo(monkeypatch):
    """Hermético al .env local y a budgets activos filtrados por otros tests."""
    from core.config import settings

    monkeypatch.setattr(settings, "tool_max_tokens_per_workflow", 80_000, raising=False)
    monkeypatch.setattr(settings, "tool_enable_token_budget", True, raising=False)
    # sin presupuesto ACTIVO (ContextVar): el banner cae al setting del test
    import tools.orchestrator as tools_orch

    monkeypatch.setattr(tools_orch, "get_token_budget_max", lambda: 0)


def test_budget_banner_aparece_al_80pct(_budget_fijo):
    _qapp()
    from desktop.maestro_tab import SessionPane

    m = SessionPane()
    m._budget_warned = False
    m._maybe_warn_token_budget({"tokens_used": 90_000})
    assert m._budget_warned is True
    assert m._status_banner.isVisibleTo(m)
    assert "Presupuesto" in m._status_banner.text()


def test_budget_banner_no_repite_y_se_rearma_por_run(_budget_fijo):
    _qapp()
    from desktop.maestro_tab import SessionPane

    m = SessionPane()
    m._budget_warned = False
    m._maybe_warn_token_budget({"tokens_used": 90_000})
    m._maybe_warn_token_budget({"tokens_used": 99_000})  # ya avisó
    assert m._status_banner.text().count("Presupuesto") == 1
    # nuevo run → re-armado
    m._set_workflow_running(True)
    assert m._budget_warned is False


def test_budget_banner_bajo_umbral_no_aparece(_budget_fijo):
    _qapp()
    from desktop.maestro_tab import SessionPane

    m = SessionPane()
    m._budget_warned = False
    m._maybe_warn_token_budget({"tokens_used": 10_000})
    assert m._budget_warned is False
    assert not m._status_banner.isVisibleTo(m)


def test_budget_banner_usa_el_techo_del_preset(monkeypatch):
    """El preset DSL puede acotar su run con budget_tokens: el banner se
    alinea al techo REAL (no al global)."""
    from core.config import settings

    monkeypatch.setattr(settings, "tool_max_tokens_per_workflow", 800_000, raising=False)
    monkeypatch.setattr(settings, "tool_enable_token_budget", True, raising=False)
    import tools.orchestrator as tools_orch

    monkeypatch.setattr(tools_orch, "get_token_budget_max", lambda: 100_000)

    _qapp()
    from desktop.maestro_tab import SessionPane

    m = SessionPane()
    m._budget_warned = False
    m._maybe_warn_token_budget({"tokens_used": 90_000})  # 90% del preset
    assert m._budget_warned is True
    assert "100,000" in m._status_banner.text()


def test_on_stats_renderiza_el_plan_del_run_y_el_badge_replan():
    """El panel muestra la entrega por subtarea (no solo pasos) y avisa que
    el plan se replanificó; sin esto el estado del run quedaba en el chat."""
    _qapp()
    from desktop.maestro_tab import SessionPane

    m = SessionPane()
    m._set_workflow_running(True)
    run_state = {
        "run_id": "r",
        "goal": "g",
        "plan": [
            {"kind": "subtask", "id": "1", "goal": "login", "status": "delivered"},
            {"kind": "subtask", "id": "2", "goal": "logout", "status": "failed"},
            {"kind": "subtask", "id": "3", "goal": "reset", "status": "unmeasured"},
            {"kind": "subtask", "id": "4", "goal": "otra", "status": "skipped"},
        ],
        "replans": [{"action": "replace"}],
        "budget": {"limit": 100, "spent": 10, "reserve_final": 5, "reserve_replan": 5},
        "memory": {},
    }
    m._on_stats({"status": "Ejecutando (DSL)", "run_state": run_state, "subtask_list": []})
    texts = [m._subtask_list.item(i).text() for i in range(m._subtask_list.count())]
    assert any("↻ replan" in t for t in texts)
    assert any("✓" in t and "login" in t for t in texts)
    assert any("✕" in t and "logout" in t for t in texts)
    assert any("⚠" in t and "reset" in t for t in texts)
    assert any("⏭" in t and "otra" in t for t in texts)
    # la barra y el diagrama hablan de SUBTAREAS del plan, no de pasos
    assert "1/4" in m._progress_bar.format()
    assert "login" in m._diagram_view.toHtml()


def test_status_detenido_apaga_el_chip():
    """`stopped` es terminal: el run no está en ejecución."""
    _qapp()
    from desktop.maestro_tab import SessionPane

    m = SessionPane()
    m._set_workflow_running(True)
    m._on_stats({"status": "Detenido"})
    assert m._activity_dot.text() == "○"
