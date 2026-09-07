# tests/test_remediacion_gui.py — cobertura de los flujos GUI del sprint (offscreen)
"""Cubre los flujos GUI añadidos en la remediación: diálogo de proyecto
previo al lanzamiento, checklist de miembros de sala y recarga de chats
abiertos por ⟳. Los QDialog.exec() se parchean — nada bloquea offscreen."""

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QListWidget  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


@pytest.fixture
def fake_projects(tmp_path, monkeypatch):
    """projects_base apuntando a tmp con dos proyectos falsos."""
    import desktop.services.project_service as ps

    base = tmp_path / "code_projects"
    base.mkdir()
    for name in ("alfa", "beta"):
        (base / name).mkdir()
    monkeypatch.setattr(ps, "projects_base", lambda ws=None: base)
    return base


def test_ask_project_accepted_selects_and_returns_name(monkeypatch, fake_projects):
    from desktop.maestro_tab import SessionPane

    _qapp()
    pane = SessionPane()
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted, raising=False)
    chosen = pane._ask_project_for_workflow("tdd")
    assert chosen == "alfa"  # preselección de la primera fila
    assert pane._current_project_root == "code_projects/alfa"


def test_ask_project_rejected_returns_none(monkeypatch, fake_projects):
    from desktop.maestro_tab import SessionPane

    _qapp()
    pane = SessionPane()
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected, raising=False)
    assert pane._ask_project_for_workflow("tdd") is None
    assert pane._current_project_root is None


def test_ask_project_without_projects_and_reject_returns_none(monkeypatch, tmp_path):
    import desktop.services.project_service as ps
    from desktop.maestro_tab import SessionPane

    _qapp()
    empty = tmp_path / "vacío"
    empty.mkdir()
    monkeypatch.setattr(ps, "projects_base", lambda ws=None: empty)
    pane = SessionPane()
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected, raising=False)
    assert pane._ask_project_for_workflow("tdd") is None


def test_ask_members_returns_checked_slugs(monkeypatch):
    from desktop.bots_tab import _ask_members

    _qapp()
    rows = [
        {"slug": "alfa", "display_name": "Alfa", "enabled": True},
        {"slug": "beta", "display_name": "Beta", "enabled": True},
        {"slug": "gama", "display_name": "Gama", "enabled": False},
    ]

    async def fake_list(include_disabled=False):
        return rows

    from core.bots import BotsService

    monkeypatch.setattr(BotsService, "list_bots", staticmethod(fake_list), raising=False)

    def fake_exec(dialog):
        listw = dialog.findChild(QListWidget)
        assert listw is not None
        assert listw.count() == 2  # gama deshabilitado NO aparece
        for i in range(2):
            listw.item(i).setCheckState(Qt.CheckState.Checked)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec, raising=False)
    assert _ask_members(None) == ["alfa", "beta"]


def test_ask_members_reject_returns_none(monkeypatch):
    from desktop.bots_tab import _ask_members

    _qapp()

    async def fake_list(include_disabled=False):
        return [{"slug": "alfa", "display_name": "Alfa", "enabled": True}]

    from core.bots import BotsService

    monkeypatch.setattr(BotsService, "list_bots", staticmethod(fake_list), raising=False)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected, raising=False)
    assert _ask_members(None) is None


def test_reload_open_conversations_reloads_only_reusable_panes(monkeypatch):
    from desktop.maestro_tab import MaestroTab, SessionPane

    _qapp()
    m = MaestroTab()
    recargadas: list[int] = []

    async def fake_load(self, conv_id):
        recargadas.append(conv_id)

    monkeypatch.setattr(SessionPane, "load_conversation", fake_load, raising=False)
    # run_async es fire-and-forget (agenda sin esperar); para asertar el
    # resultado el test ejecuta la corrutina al gusto.
    import asyncio

    monkeypatch.setattr(
        "desktop.async_helpers.run_async", lambda coro: asyncio.run(coro), raising=False
    )
    p1 = m._panes[0]
    p2 = m.add_session()
    p1._conversation_id = 7
    p2._conversation_id = None
    assert m._reload_open_conversations() == 1
    assert recargadas == [7]
    assert p1.busy_loading is False
