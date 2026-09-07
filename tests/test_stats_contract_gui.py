# tests/test_stats_contract_gui.py
"""Contrato de stats del agent-loop → GUI.

- Los parciales del agent-loop llevan tokens_used (chip en vivo, no solo
  boundaries) y su status final NO es terminal del workflow ("Agent listo",
  no "completed" que apagaba el punto ●/○ a mitad del run).
- `_on_stats` ignora emits tardíos tras ⏹/fin (no re-encienden indicadores).
"""

from typing import cast

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


class TestAgentLoopStatsPayload:
    def test_payload_lleva_tokens_y_files(self):
        from orchestration.loop import _agent_loop_stats

        payload = _agent_loop_stats(
            "Agent iteration 2/8", "developer", actions_taken=3, files_written=["a.py"]
        )
        assert payload["status"] == "Agent iteration 2/8"
        assert payload["current_agent"] == "developer"
        assert payload["actions_taken"] == 3
        assert payload["files_written"] == ["a.py"]
        assert isinstance(payload["tokens_used"], int)  # chip en vivo

    def test_status_final_del_loop_no_es_terminal(self):
        """El workflow puede seguir (loop DSL, subtareas) tras un agent-loop
        que termina — el status parcial no debe apagar el indicador ●/○."""
        from desktop.maestro_tab import _ACTIVITY_TERMINAL_PREFIXES
        from orchestration.loop import _agent_loop_stats

        payload = _agent_loop_stats("Agent listo (3 iter)", "developer")
        status = str(payload["status"]).lower()
        assert not status.startswith(_ACTIVITY_TERMINAL_PREFIXES)

    def test_el_viejo_completed_parcial_era_terminal_regresion(self):
        from desktop.maestro_tab import _ACTIVITY_TERMINAL_PREFIXES

        assert "completed".startswith(_ACTIVITY_TERMINAL_PREFIXES)


class TestOnStatsGuardTardios:
    def _pane(self):
        from desktop.maestro_tab import SessionPane

        return SessionPane()

    def test_emit_tardio_tras_fin_no_reenciende_indicador(self):
        _qapp()
        pane = self._pane()
        try:
            pane._set_workflow_running(False)
            # emit en vuelo que llega DESPUÉS del ⏹/fin con status "running"
            pane._on_stats({"status": "Ejecutando (DSL)", "subtasks_total": 1})
            assert (
                pane._activity_dot.text() == "○"
            ), "un emit tardío re-encendió el indicador tras el fin del run"
        finally:
            pane.deleteLater()

    def test_stats_durante_run_sí_actualizan(self):
        _qapp()
        pane = self._pane()
        try:
            pane._set_workflow_running(True)
            pane._on_stats({"status": "Ejecutando (DSL)", "subtasks_total": 1})
            assert pane._activity_dot.text() == "●"
        finally:
            pane._set_workflow_running(False)
            pane.deleteLater()
