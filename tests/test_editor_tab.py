# tests/test_editor_tab.py — Pestaña Editor: multi-archivo, modo libre, guardado atómico
"""Contratos del componente de edición:

- Multi-pestaña: un CodeEditor por archivo; reabrir enfoca sin duplicar.
- Marcador "•" en el título de la pestaña mientras hay cambios sin guardar.
- Guardado atómico (tmp + os.replace; sin residuos .morphix-tmp).
- Contención: en modo proyecto se bloquea guardar fuera; en modo carpeta
  libre se permite donde navegó el usuario.
- Guards de binario/tamaño muestran aviso y no abren pestaña.
- Cierre de pestaña y cambio de proyecto preguntan por cambios sin guardar.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton  # noqa: E402

from core.path_resolver import paths  # noqa: E402


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(paths, "code_projects_dir", lambda ws, r: root)
    return root


def _tab():
    from desktop.editor_tab import EditorTab

    return EditorTab()


def test_open_file_creates_tab_and_reopen_focuses(proj):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    (proj / "a.py").write_text("x = 1\n", encoding="utf-8")

    tab.open_project_file("a.py")
    assert tab._tabs.count() == 1
    ed = tab._tabs.widget(0)
    assert ed.toPlainText() == "x = 1\n"
    assert ed.path() == proj / "a.py"

    # reabrir no duplica y conserva la instancia (y su edición)
    ed.setPlainText("x = 2\n")
    tab.open_project_file("a.py")
    assert tab._tabs.count() == 1
    assert tab._tabs.widget(0) is ed
    assert ed.toPlainText() == "x = 2\n", "reabrir no debe recargar y perder la edición"


def test_dirty_marker_and_atomic_save(proj):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    f = proj / "b.py"
    f.write_text("orig\n", encoding="utf-8")
    tab.open_project_file("b.py")
    ed = tab._tabs.widget(0)

    assert not tab._save_btn.isEnabled()
    ed.setPlainText("editado\n")
    assert tab._save_btn.isEnabled(), "editar debe habilitar Guardar"
    assert "•" in tab._tabs.tabText(0), "pestaña sucia debe mostrar marcador"

    tab._save()
    assert f.read_text(encoding="utf-8") == "editado\n"
    assert not tab._save_btn.isEnabled()
    assert "•" not in tab._tabs.tabText(0)
    leftovers = list(proj.glob("*.morphix-tmp")) + list(proj.glob("*.tmp"))
    assert not leftovers, f"guardado atómico no debe dejar residuos: {leftovers}"


def test_project_mode_blocks_save_outside(proj, tmp_path):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    outside = tmp_path / "out.txt"
    outside.write_text("fuera\n", encoding="utf-8")

    tab._open_file(outside)
    ed = tab._tabs.widget(0)
    ed.setPlainText("trampa\n")
    tab._save()
    assert "fuera del proyecto" in tab._status_label.text().lower()
    assert outside.read_text(encoding="utf-8") == "fuera\n", "no debió escribir fuera"


def test_free_folder_mode_allows_save(proj, tmp_path):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    libre = tmp_path / "libre"
    libre.mkdir()
    tab.open_folder(libre)

    assert tab._mode == "free"
    f = libre / "nota.md"
    f.write_text("hola\n", encoding="utf-8")
    tab._open_file(f)
    ed = tab._tabs.widget(0)
    ed.setPlainText("editado libre\n")
    tab._save()
    assert f.read_text(encoding="utf-8") == "editado libre\n"


def test_binary_file_shows_notice_no_tab(proj):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    (proj / "bin.dat").write_bytes(b"PK\x00\x03binario")

    tab.open_project_file("bin.dat")
    assert tab._tabs.count() == 0
    assert "binario" in tab._status_label.text().lower()


def test_large_file_shows_notice_no_tab(proj, monkeypatch):
    _qapp()
    import desktop.editor_tab as et

    monkeypatch.setattr(et, "MAX_FILE_SIZE", 10)
    tab = _tab()
    tab.set_project("repo", "main")
    (proj / "big.txt").write_text("y" * 100, encoding="utf-8")

    tab.open_project_file("big.txt")
    assert tab._tabs.count() == 0
    assert tab._status_label.text() != ""


def test_close_dirty_tab_cancel_keeps(proj, monkeypatch):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    f = proj / "c.py"
    f.write_text("v1\n", encoding="utf-8")
    tab.open_project_file("c.py")
    tab._tabs.widget(0).setPlainText("v2\n")

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    tab._close_tab(0)
    assert tab._tabs.count() == 1, "Cancel debe conservar la pestaña"


def test_close_dirty_tab_discard_closes_without_write(proj, monkeypatch):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    f = proj / "d.py"
    f.write_text("v1\n", encoding="utf-8")
    tab.open_project_file("d.py")
    tab._tabs.widget(0).setPlainText("v2\n")

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    tab._close_tab(0)
    assert tab._tabs.count() == 0
    assert f.read_text(encoding="utf-8") == "v1\n", "Discard no debe escribir"


def test_set_project_with_dirty_cancel_blocks_switch(proj, monkeypatch):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    (proj / "e.py").write_text("1\n", encoding="utf-8")
    tab.open_project_file("e.py")
    tab._tabs.widget(0).setPlainText("2\n")

    answers = iter([QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Discard])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: next(answers))

    tab.set_project("otro", "main")  # Cancel → bloquea el switch
    assert tab._project_root == "repo", "Cancel debe impedir cambiar de proyecto"
    assert tab._tabs.count() == 1

    tab.set_project("otro", "main")  # Discard → procede y cierra pestañas
    assert tab._project_root == "otro"
    assert tab._tabs.count() == 0


def test_editor_tabs_use_theme_style(proj):
    _qapp()
    from desktop.theme import StyleFactory

    tab = _tab()
    assert tab._tabs.styleSheet() == StyleFactory.editor_tabs()


# ── Navegación breadcrumb + doble clic ──────


def _proxy_index(tab, path):
    src = tab._fs_model.index(str(path))
    return tab._proxy.mapFromSource(src)


def test_open_folder_btn_enabled_without_project(proj):
    _qapp()
    tab = _tab()
    tab._set_project(None)
    assert tab._open_folder_btn.isEnabled(), "📂 debe estar disponible sin proyecto"
    assert tab._new_file_btn.isEnabled(), "con navegación libre activa hay raíz válida"
    assert tab._new_dir_btn.isEnabled()
    assert tab._nav_root == Path.home()


def test_single_click_does_not_open(proj):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    f = proj / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    tab._tree.clicked.emit(_proxy_index(tab, f))
    assert tab._tabs.count() == 0, "un clic NO debe abrir archivos (frágil)"


def test_double_click_opens_file(proj):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    f = proj / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    tab._tree.doubleClicked.emit(_proxy_index(tab, f))
    assert tab._tabs.count() == 1
    assert tab._tabs.widget(0).toPlainText() == "x = 1\n"


def test_double_click_folder_descends_and_breadcrumb_navigates(proj):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    sub = proj / "sub"
    sub.mkdir()
    (sub / "inner.py").write_text("y = 2\n", encoding="utf-8")

    tab._tree.doubleClicked.emit(_proxy_index(tab, sub))
    assert tab._nav_root == sub, "doble clic en carpeta debe descender"
    assert tab._mode == "project", "descender dentro del proyecto conserva el modo"

    # breadcrumb: [⬆] [miga raíz] [sub] — clic en la miga raíz vuelve al proyecto
    widgets = [tab._crumbs.itemAt(i).widget() for i in range(tab._crumbs.count())]
    crumbs = [w for w in widgets if isinstance(w, QPushButton)]
    assert any(proj.name in w.text() for w in crumbs), "la miga del proyecto debe existir"
    assert any(
        isinstance(w, (QPushButton, QLabel)) and "sub" in w.text() for w in widgets
    ), "el subdirectorio debe aparecer en el breadcrumb"
    root_crumb = next(w for w in crumbs if proj.name in w.text())
    root_crumb.click()
    assert tab._nav_root == proj, "clic en la miga del proyecto re-ubica el árbol"


def test_ascend_above_project_warns_then_free(proj, tmp_path, monkeypatch):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    tab._ascend()
    assert tab._mode == "free", "subir sobre la raíz pasa a modo libre (con aviso)"
    assert tab._nav_root == tmp_path, "la raíz de navegación es el padre del proyecto"


def test_ascend_above_project_cancel_stays_project(proj, tmp_path, monkeypatch):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    tab._ascend()
    assert tab._mode == "project", "cancelar el aviso conserva el modo proyecto"
    assert tab._nav_root == proj


def test_ascend_within_project_no_prompt(proj, monkeypatch):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    sub = proj / "sub"
    sub.mkdir()
    tab._set_tree_root(sub)

    def _explode(*a, **k):
        raise AssertionError("subir dentro del proyecto no debe pedir confirmación")

    monkeypatch.setattr(QMessageBox, "question", _explode)
    tab._ascend()
    assert tab._nav_root == proj and tab._mode == "project"


def test_free_mode_breadcrumb_shows_machine_chain(proj, tmp_path):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    libre = tmp_path / "muy" / "profundo"
    libre.mkdir(parents=True)
    tab.open_folder(libre)
    crumbs = [tab._crumbs.itemAt(i).widget() for i in range(tab._crumbs.count())]
    texts = [w.text() for w in crumbs if isinstance(w, (QPushButton, QLabel))]
    assert any("muy" in t for t in texts) and any("profundo" in t for t in texts)
    # descender libre: doble clic en subdir re-ubica sin cambiar de modo
    (libre / "hoja").mkdir()
    tab._tree.doubleClicked.emit(_proxy_index(tab, libre / "hoja"))
    assert tab._nav_root == libre / "hoja" and tab._mode == "free"


def test_breadcrumb_button_uses_theme_qss():
    _qapp()
    from desktop.theme import StyleFactory

    qss = StyleFactory.breadcrumb_button()
    assert "QPushButton" in qss and "font-size" in qss


# ── Breadcrumb siempre disponible + volver al proyecto (iter 2) ───────────


def test_breadcrumb_available_without_project():
    _qapp()
    tab = _tab()  # __init__ ya llama _set_project(None)
    assert tab._mode == "free", "sin proyecto hay navegación libre"
    assert tab._nav_root == Path.home(), "la raíz de navegación arranca en home"
    assert tab._crumbs.count() >= 1, "breadcrumb visible sin proyecto"
    assert tab._ascend_btn.isEnabled(), "⬆ disponible sin proyecto"


def test_navigate_via_breadcrumb_without_project():
    _qapp()
    tab = _tab()
    tab._navigate_to(Path.home().parent)
    assert tab._nav_root == Path.home().parent
    assert tab._crumbs.count() >= 1


def test_back_to_project_button_roundtrip(proj, tmp_path):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    libre = tmp_path / "libre"
    libre.mkdir()
    tab.open_folder(libre)
    assert tab._back_btn.isEnabled(), "en modo libre con proyecto, Volver habilitado"
    tab._back_btn.click()
    assert tab._mode == "project"
    assert tab._nav_root == proj, "Volver re-ubica el árbol en el proyecto"


def test_back_button_disabled_without_project():
    _qapp()
    tab = _tab()
    assert not tab._back_btn.isEnabled(), "sin proyecto no hay a dónde volver"


def test_back_button_disabled_inside_project(proj):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    assert not tab._back_btn.isEnabled()


def test_open_folder_dialog_always_picks(proj, monkeypatch):
    _qapp()
    tab = _tab()
    tab.set_project("repo", "main")
    destino = proj / "otro"
    destino.mkdir()
    monkeypatch.setattr(
        "desktop.editor_tab.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(destino),
    )
    tab._pick_folder()
    assert tab._mode == "free" and tab._nav_root == destino
