# tests/test_phase_cards.py
"""PhaseCards NO debe borrar el diagrama con stats parciales.

El agent-loop emite stats parciales SIN subtask_list ("Agent Loop started",
"Agent iteration N/M") — con `data.get("subtask_list") or []` el diagrama se
limpiaba a "Workflow vacío" y volvía a dibujarse en el próximo boundary
(parpadeo / "señales que se saltan").
"""

from typing import cast

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.widgets.phase_cards import PhaseCards  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


FULL = {
    "subtask_list": [
        {"name": "ciclo#1 ▸ implementar", "status": "done"},
        {"name": "ciclo#2 ▸ implementar", "status": "running"},
    ],
    "phase": "Iteración",
}

PARCIAL_SIN_LISTA = {"status": "Agent iteration 2/15", "current_agent": "developer"}


def test_parcial_sin_subtask_list_conserva_diagrama():
    _qapp()
    w = PhaseCards()
    w.update_from_stats(FULL)
    html_antes = w.toHtml()
    assert "Workflow vacío" not in html_antes

    w.update_from_stats(PARCIAL_SIN_LISTA)
    assert "Workflow vacío" not in w.toHtml(), "el parcial SIN lista borró el diagrama"
    assert html_antes == w.toHtml(), "el parcial debe dejar el diagrama intacto"


def test_lista_vacia_explícita_también_conserva():
    _qapp()
    w = PhaseCards()
    w.update_from_stats(FULL)
    html_antes = w.toHtml()
    w.update_from_stats({"subtask_list": []})
    assert w.toHtml() == html_antes
