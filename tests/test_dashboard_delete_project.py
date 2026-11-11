# tests/test_dashboard_delete_project.py
"""El botón Eliminar del Dashboard: cableado, seguridad y limpieza de panes.

La semántica del borrado vive en project_service (test_project_service);
aquí se guarda lo que es de la GUI: que el botón existe, que el handler
limpia los punteros de las panes al proyecto borrado y que el getter del
proyecto actual lee de la PANE activa (el tab solo enruta — leerlo del tab
devolvía siempre None y el marcador ● del árbol jamás se renderizó).
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pathlib
import re

_FUENTE = pathlib.Path("desktop/dashboard_tab.py").read_text(encoding="utf-8")
_FUENTE_MAESTRO = pathlib.Path("desktop/maestro_tab.py").read_text(encoding="utf-8")


def test_boton_eliminar_en_las_altas_de_proyectos():
    """Las altas de proyectos ofrecen nuevo/importar/clonar/eliminar."""
    patron = re.compile(
        r'\("＋ Nuevo", self\._create_project\),\s*'
        r'\("⌂ Importar", self\._import_project\),\s*'
        r'\("⤓ Clonar", self\._clone_project\),\s*'
        r'\("🗑 Eliminar", self\._delete_project\),',
        re.S,
    )
    assert patron.search(_FUENTE), "las altas de proyectos deben incluir Eliminar"


def test_handler_confirma_antes_de_borrar():
    """El handler pide el nombre, muestra tamaño/archivos y usa el servicio
    (delete_project) — nunca borra sin confirmación."""
    assert "QInputDialog.getText" in _FUENTE
    assert "QMessageBox.question" in _FUENTE
    assert "delete_project(name)" in _FUENTE
    assert "project_stats(name)" in _FUENTE
    # el No es el default: confirmar es opt-in
    assert "QMessageBox.StandardButton.No,\n" in _FUENTE or re.search(
        r"StandardButton\.No\s*,\s*$", _FUENTE, re.M
    )


def test_borrado_limpia_punteros_de_las_panes():
    """Cualquier pane que apuntara al proyecto borrado queda sin proyecto."""
    assert (
        "_clear_project_selection()" in _FUENTE
    ), "el handler debe limpiar la selección de las panes tras borrar"
    # el método existe en SessionPane y resetea puntero+botón+señal
    assert "def _clear_project_selection" in _FUENTE_MAESTRO
    cuerpo = _FUENTE_MAESTRO.split("def _clear_project_selection", 1)[1][:900]
    assert "self._current_project_root = None" in cuerpo
    assert "sin proyecto" in cuerpo
    assert 'project_changed.emit("")' in cuerpo


def test_current_project_name_le_de_la_pane_activa():
    """El estado del proyecto vive en la pane activa: leerlo del tab era el
    bug latente que dejaba el marcador ● del árbol siempre apagado."""
    cuerpo = _FUENTE.split("def _current_project_name", 1)[1][:500]
    assert "_active_pane" in cuerpo, "debe resolver la pane activa"
    assert "_current_project_root" in cuerpo
    # y NO lee el atributo directo del tab
    assert 'getattr(maestro, "_current_project_root"' not in cuerpo


class TestClearProjectSelection:
    def test_resetea_puntero_boton_y_senal(self):
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from desktop.events import get_signals
        from desktop.maestro_tab import SessionPane

        recibidas: list[str] = []
        get_signals().project_changed.connect(lambda v: recibidas.append(v))
        try:
            stub = SimpleNamespace(
                _current_project_root="code_projects/viejo",
                _project_btn=MagicMock(),
                # el clear también apaga el preload (sin proyecto no hay nada
                # que pre-cargar) — el stub necesita la acción
                _preload_action=MagicMock(),
            )
            SessionPane._clear_project_selection(stub)
            assert stub._current_project_root is None
            stub._project_btn.setText.assert_called_once()
            assert "sin proyecto" in stub._project_btn.setText.call_args[0][0]
            stub._preload_action.setEnabled.assert_called_once_with(False)
            assert recibidas == [""]
        finally:
            # desconectar: el singleton de señales vive toda la sesión
            try:
                get_signals().project_changed.disconnect()
            except (TypeError, RuntimeError):
                pass
