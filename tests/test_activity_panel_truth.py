"""Verdad y orden del panel Actividad del Maestro.

Cubre los cuatro fallos de fondo que hacían que las secciones mostraran datos
viejos o contradictorios:

- El panel no se reseteaba al empezar un run (arrastraba progreso/subtareas/
  archivos/diagrama del anterior).
- La barra de progreso cacheaba por porcentaje y no usaba el denominador del
  reporte (descartes por contrato).
- Los archivos se reemplazaban con la lista LOCAL de cada agent-loop.
- El Log no trazaba el avance y no escapaba HTML; Bash solo mostraba el
  último comando.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from typing import cast  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def _pane(running: bool = True):
    from desktop.maestro_tab import SessionPane

    _qapp()
    pane = SessionPane()
    if running:
        pane._set_workflow_running(True)
    return pane


def _plan_item(item_id: str, goal: str, status: str, **extra) -> dict:
    base = {
        "kind": "subtask",
        "id": item_id,
        "goal": goal,
        "path": f"l#{item_id}",
        "status": status,
        "files_written": [],
        "notes": [],
        "satisfied_by": None,
        "verification": None,
        "last_error": None,
    }
    base.update(extra)
    return base


def _run_state(plan: list[dict], replans: list | None = None) -> dict:
    return {
        "run_id": "r",
        "goal": "g",
        "plan": plan,
        "budget": {"limit": 0, "spent": 0, "reserve_final": 0, "reserve_replan": 0},
        "replans": replans or [],
        "memory": {},
    }


def test_begin_run_resets_previous_run_state():
    pane = _pane()
    try:
        pane._on_stats(
            {
                "status": "Ejecutando (DSL)",
                "subtask_list": [{"name": "viejo", "status": "running"}],
                "files_written": ["viejo.py"],
                "subtasks_total": 2,
                "subtasks_completed": 1,
            }
        )
        assert pane._progress_bar.value() == 50
        assert pane._files_written_list.count() == 1

        pane._begin_activity_run("consulta nueva")

        assert pane._progress_bar.value() == 0
        assert pane._progress_bar.format() == "—"
        assert pane._subtask_list.count() == 0
        assert pane._files_written_list.count() == 0
        assert "viejo" not in pane._diagram_view.toPlainText()
        assert "Diagrama aquí" in pane._diagram_view.toPlainText()
        assert pane._activity_dot.text() == "●"
        assert "Run iniciado" in pane._status_log_view.toPlainText()
        assert "—" in pane.stat_chips._labels["status"].text()
    finally:
        pane.deleteLater()


def test_progreso_usa_el_denominador_del_reporte():
    """Un descarte por contrato sin evidencia no es trabajo pendiente.

    RunState.counts() excluye ese ítem del denominador: la barra debe dar
    100% con las 2 subtareas entregables, no quedarse en 66% para siempre.
    """
    pane = _pane()
    try:
        plan = [
            _plan_item("1", "a", "delivered"),
            _plan_item(
                "2",
                "b",
                "skipped",
                notes=["descartado por contrato del preset (no ejecutado)"],
            ),
            _plan_item("3", "c", "delivered"),
        ]
        pane._on_stats({"status": "Ejecutando (DSL)", "run_state": _run_state(plan)})
        assert pane._progress_bar.value() == 100
        assert pane._progress_bar.format() == "2/2 subtareas"
    finally:
        pane.deleteLater()


def test_progreso_se_repinta_si_cambian_los_conteos_con_el_mismo_pct():
    pane = _pane()
    try:
        plan_2 = [_plan_item("1", "a", "delivered"), _plan_item("2", "b", "pending")]
        pane._on_stats({"status": "Ejecutando (DSL)", "run_state": _run_state(plan_2)})
        assert pane._progress_bar.format() == "1/2 subtareas"

        plan_4 = [
            _plan_item("1", "a", "delivered"),
            _plan_item("2", "b", "delivered"),
            _plan_item("3", "c", "pending"),
            _plan_item("4", "d", "pending"),
        ]
        # mismo 50%, otra unidad de entrega: el formato NO puede quedarse en 1/2
        pane._on_stats({"status": "Ejecutando (DSL)", "run_state": _run_state(plan_4)})
        assert pane._progress_bar.format() == "2/4 subtareas"
    finally:
        pane.deleteLater()


def test_total_cero_resetea_la_barra():
    pane = _pane()
    try:
        pane._on_stats({"status": "Ejecutando (DSL)", "subtasks_total": 3, "subtasks_completed": 3})
        assert pane._progress_bar.value() == 100
        # cierre de tool directa: no hay unidades de entrega reales
        pane._on_stats({"status": "Completado (tool directa)", "subtasks_total": 0})
        assert pane._progress_bar.value() == 0
        assert pane._progress_bar.format() == "—"
    finally:
        pane.deleteLater()


def test_plan_solo_fases_cae_a_los_pasos_emitidos():
    """Un plan sin subtareas (solo fases) no puede dejar Subtareas vacía."""
    pane = _pane()
    try:
        run_state = _run_state(
            [{"kind": "phase", "id": "rojo", "goal": "rojo", "status": "running"}]
        )
        pane._on_stats(
            {
                "status": "Ejecutando (DSL)",
                "run_state": run_state,
                "subtask_list": [{"name": "rojo", "status": "running"}],
            }
        )
        texts = [pane._subtask_list.item(i).text() for i in range(pane._subtask_list.count())]
        assert any("rojo" in t for t in texts)
        assert "rojo" in pane._diagram_view.toPlainText()
    finally:
        pane.deleteLater()


def test_archivos_se_acumulan_entre_parciales():
    """La lista local de un agent-loop no borra los archivos de steps previos."""
    pane = _pane()
    try:
        pane._on_stats({"status": "Agent iteration 1/8", "files_written": ["a.py"]})
        # loop nuevo arranca con su lista local vacía
        pane._on_stats({"status": "Agent Loop started", "files_written": []})
        pane._on_stats({"status": "Agent iteration 2/8", "files_written": ["b.py"]})
        texts = [
            pane._files_written_list.item(i).text() for i in range(pane._files_written_list.count())
        ]
        assert texts == ["a.py", "b.py"]
    finally:
        pane.deleteLater()


def test_diagrama_usa_el_plan_y_se_resetea_por_run():
    pane = _pane()
    try:
        plan = [_plan_item("1", "login", "delivered")]
        pane._on_stats({"status": "Ejecutando (DSL)", "run_state": _run_state(plan)})
        assert "login" in pane._diagram_view.toPlainText()

        pane._begin_activity_run("otra")
        assert "login" not in pane._diagram_view.toPlainText()
        assert "Diagrama aquí" in pane._diagram_view.toPlainText()
    finally:
        pane.deleteLater()


def test_log_traza_transiciones_de_subtareas_en_orden():
    pane = _pane()
    try:
        plan = [_plan_item("1", "login", "running")]
        pane._on_stats({"status": "Ejecutando (DSL)", "run_state": _run_state(plan)})
        plan = [_plan_item("1", "login", "delivered")]
        pane._on_stats({"status": "Ejecutando (DSL)", "run_state": _run_state(plan)})
        # mismo estado: no se duplica la línea
        pane._on_stats({"status": "Ejecutando (DSL)", "run_state": _run_state(plan)})
        pane._on_stats({"status": "Completado (DSL)", "run_state": _run_state(plan)})

        log = pane._status_log_view.toPlainText()
        assert log.count("login — running") == 1
        assert log.count("login — delivered") == 1
        assert log.index("login — running") < log.index("login — delivered")
        assert "■ Completado (DSL)" in log
    finally:
        pane.deleteLater()


def test_log_escapa_html_y_conserva_saltos():
    pane = _pane()
    try:
        pane._on_system("<b>no soy negrita</b>\nsegunda linea")
        log = pane._status_log_view.toPlainText()
        assert "<b>no soy negrita</b>" in log
        assert "segunda linea" in log
        assert "<b>no soy negrita</b>" not in pane._status_log_view.toHtml()
    finally:
        pane.deleteLater()


def test_bash_acumula_en_orden_y_se_limpia_por_run():
    pane = _pane()
    try:
        pane._on_system("[bash_manager]\ncomando uno")
        pane._on_system("[bash_manager]\ncomando dos")
        text = pane.bash_panel.output.toPlainText()
        assert "[bash_manager]" not in text
        assert text.index("comando uno") < text.index("comando dos")

        pane._begin_activity_run("nueva consulta")
        assert pane.bash_panel.output.toPlainText() == ""
    finally:
        pane.deleteLater()


def test_fin_sin_estado_terminal_cierra_el_chip():
    pane = _pane()
    try:
        pane._on_stats({"status": "Ejecutando (DSL)"})
        assert "Ejecutando" in pane.stat_chips._labels["status"].text()

        pane._runner.on_finish()

        assert pane._activity_dot.text() == "○"
        assert "Finalizado" in pane.stat_chips._labels["status"].text()
        assert "sin estado final" in pane._status_log_view.toPlainText()
    finally:
        pane.deleteLater()


def test_fin_con_terminal_no_pisa_el_estado():
    pane = _pane()
    try:
        pane._on_stats({"status": "Completado (DSL)"})
        pane._runner.on_finish()
        assert "Completado" in pane.stat_chips._labels["status"].text()
        assert "Finalizado" not in pane.stat_chips._labels["status"].text()
    finally:
        pane.deleteLater()


def test_elegir_agente_refresca_el_tab_bash(monkeypatch):
    pane = _pane()
    try:
        calls: list[bool] = []
        monkeypatch.setattr(pane, "_refresh_detail_tabs_for_workflow", lambda: calls.append(True))
        pane._select_agent("developer")
        assert calls, "el tab Bash quedó con el allowlist del agente anterior"
    finally:
        pane.deleteLater()


def test_diagrama_agrupa_subtareas_por_fase_del_plan():
    """El plan del motor es plano: la fase se deriva del prefijo del path.

    Sin esto el Diagrama nunca mostraba encabezados de fase en DSL (el
    contrato de stats no emite `phase`).
    """
    pane = _pane()
    try:
        plan = [
            {"kind": "phase", "id": "ciclo", "goal": "ciclo", "path": "ciclo", "status": "running"},
            _plan_item("1", "uno", "delivered", path="ciclo#1"),
            _plan_item("2", "dos", "running", path="ciclo#2"),
        ]
        pane._on_stats({"status": "Ejecutando (DSL)", "run_state": _run_state(plan)})
        text = pane._diagram_view.toPlainText()
        assert "ciclo" in text
        assert text.index("ciclo") < text.index("uno") < text.index("dos")
    finally:
        pane.deleteLater()


def test_fin_de_chat_apaga_el_dot_y_marca_el_estado():
    """La ruta de chat directo no pasa por WorkflowRunner.on_finish: sin este
    cierre el dot ● de ACTIVIDAD quedaba encendido tras cada respuesta."""
    pane = _pane()
    try:
        pane._on_stats({"status": "Agent listo (3 iter)", "current_agent": "developer"})
        assert pane._activity_dot.text() == "●"

        pane._on_run_finished("Completado (chat)")

        assert pane._activity_dot.text() == "○"
        assert "Completado (chat)" in pane.stat_chips._labels["status"].text()
        assert "■ Completado (chat)" in pane._status_log_view.toPlainText()
        assert "sin estado final" not in pane._status_log_view.toPlainText()
    finally:
        pane.deleteLater()


@pytest.mark.asyncio
async def test_run_direct_agent_cierra_la_actividad(monkeypatch):
    from unittest.mock import AsyncMock

    pane = _pane()
    try:
        monkeypatch.setattr(
            pane._runner, "run_direct_agent", AsyncMock(return_value="respuesta del agente")
        )
        monkeypatch.setattr(
            "core.repositories.conversation_repository.ConversationRepository.save",
            AsyncMock(return_value=7),
        )
        monkeypatch.setattr(
            "orchestration.finalizer._extract_personal_facts",
            AsyncMock(return_value={}),
        )

        await pane._run_direct_agent("hola", "developer")

        assert pane.is_workflow_running() is False
        assert pane._activity_dot.text() == "○"
        assert "Completado (chat)" in pane.stat_chips._labels["status"].text()
    finally:
        pane.deleteLater()


@pytest.mark.asyncio
async def test_pausa_no_se_reporta_como_cierre_perdido():
    from unittest.mock import MagicMock

    pane = _pane()
    try:
        pane._on_stats({"status": "Ejecutando (DSL)"})
        await pane._on_runner_pause(MagicMock(), "¿Qué formato?")
        pane._runner.on_finish()

        assert "Pausa" in pane.stat_chips._labels["status"].text()
        assert "sin estado final" not in pane._status_log_view.toPlainText()
        assert pane._activity_dot.text() == "○"
    finally:
        pane.deleteLater()


def test_chip_de_fase_se_deriva_del_plan():
    """El contrato DSL no emite `phase`: el chip se deriva del plan en curso."""
    pane = _pane()
    try:
        plan = [
            {"kind": "phase", "id": "ciclo", "goal": "ciclo", "path": "ciclo", "status": "running"},
            _plan_item("1", "uno", "running", path="ciclo#1"),
        ]
        pane._on_stats({"status": "Ejecutando (DSL)", "run_state": _run_state(plan)})
        assert "ciclo" in pane.stat_chips._labels["phase"].text()
    finally:
        pane.deleteLater()


def _collab_plan(status: str = "running") -> list[dict]:
    """Plan real de collaborative: loop ciego `rondas` + fase `sintesis`."""
    return [
        {
            "kind": "subtask",
            "id": "rondas",
            "goal": "rondas",
            "path": "rondas",
            "status": status,
        },
        {
            "kind": "phase",
            "id": "sintesis",
            "goal": "sintesis",
            "path": "sintesis",
            "status": "pending",
        },
    ]


def test_loop_ciego_muestra_sus_rondas_bajo_la_subtarea():
    """Un loop sin over es 1 unidad de entrega, pero sus rondas son el avance
    real: sin las filas internas la sección quedaba con una sola línea fija."""
    pane = _pane()
    try:
        steps = [
            {"name": "rondas", "status": "running"},
            {"name": "rondas#1 ▸ opinion_developer", "status": "running"},
        ]
        pane._on_stats(
            {
                "status": "Ejecutando (DSL)",
                "run_state": _run_state(_collab_plan()),
                "subtask_list": steps,
            }
        )
        texts = [pane._subtask_list.item(i).text() for i in range(pane._subtask_list.count())]
        assert any("1. rondas" in t for t in texts)
        assert any("rondas#1 ▸ opinion_developer" in t for t in texts)

        # la ronda 1 avanza: el paso cambia de estado y aparece la ronda 2
        steps = [
            {"name": "rondas", "status": "running"},
            {"name": "rondas#1 ▸ opinion_developer", "status": "completed"},
            {"name": "rondas#1 ▸ opinion_analista", "status": "completed"},
            {"name": "rondas#2 ▸ opinion_developer", "status": "running"},
        ]
        pane._on_stats(
            {
                "status": "Ejecutando (DSL)",
                "run_state": _run_state(_collab_plan()),
                "subtask_list": steps,
            }
        )
        texts = [pane._subtask_list.item(i).text() for i in range(pane._subtask_list.count())]
        assert any("✓" in t and "rondas#1 ▸ opinion_developer" in t for t in texts)
        assert any("rondas#2 ▸ opinion_developer" in t for t in texts)
        # el loop sigue running: no puede mostrarse entregado con rondas vivas
        assert "✓  1. rondas" not in texts
    finally:
        pane.deleteLater()


def test_loop_con_over_no_duplica_los_pasos_del_ciclo():
    """El ciclo de un loop con over YA es una subtarea: sus pasos no se
    anidan (la lista no duplica trabajo)."""
    pane = _pane()
    try:
        plan = [_plan_item("1", "crear login", "running", path="subtareas#1")]
        steps = [
            {"name": "subtareas#1 ▸ implementar", "status": "running"},
        ]
        pane._on_stats(
            {
                "status": "Ejecutando (DSL)",
                "run_state": _run_state(plan),
                "subtask_list": steps,
            }
        )
        texts = [pane._subtask_list.item(i).text() for i in range(pane._subtask_list.count())]
        assert len(texts) == 1
        assert "crear login" in texts[0]
    finally:
        pane.deleteLater()


def test_diagrama_incluye_las_rondas_del_loop_ciego():
    pane = _pane()
    try:
        steps = [
            {"name": "rondas", "status": "running"},
            {"name": "rondas#1 ▸ opinion_developer", "status": "completed"},
            {"name": "rondas#1 ▸ opinion_analista", "status": "running"},
        ]
        pane._on_stats(
            {
                "status": "Ejecutando (DSL)",
                "run_state": _run_state(_collab_plan()),
                "subtask_list": steps,
            }
        )
        text = pane._diagram_view.toPlainText()
        assert "rondas#1 ▸ opinion_developer" in text
        assert "rondas#1 ▸ opinion_analista" in text
    finally:
        pane.deleteLater()


def test_chip_de_fase_muestra_la_ronda_del_loop_ciego():
    pane = _pane()
    try:
        steps = [
            {"name": "rondas", "status": "running"},
            {"name": "rondas#2 ▸ opinion_developer", "status": "running"},
        ]
        pane._on_stats(
            {
                "status": "Ejecutando (DSL)",
                "run_state": _run_state(_collab_plan()),
                "subtask_list": steps,
            }
        )
        assert "ronda 2" in pane.stat_chips._labels["phase"].text()
    finally:
        pane.deleteLater()


def test_diagrama_incluye_la_fase_final_sin_subtareas():
    """La fase de cierre (sintesis) no es una subtarea: sin su tarjeta el
    diagrama se quedaba en "rondas delivered" mientras el run seguía."""
    pane = _pane()
    try:
        plan = _collab_plan()
        plan[1]["status"] = "running"
        steps = [{"name": "rondas", "status": "completed"}]
        pane._on_stats(
            {
                "status": "Ejecutando (DSL)",
                "run_state": _run_state(plan),
                "subtask_list": steps,
            }
        )
        text = pane._diagram_view.toPlainText()
        assert "sintesis" in text
        assert "RUNNING" in text
    finally:
        pane.deleteLater()
