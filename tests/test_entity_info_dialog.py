"""Tests del botón ⓘ y la ventana EntityInfoDialog."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from typing import cast  # noqa: E402

from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


# ── Dialog: workflow ──


def test_for_workflow_template_real():
    from desktop.widgets.entity_info_dialog import EntityInfoDialog

    _qapp()
    dlg = EntityInfoDialog.for_workflow(None, "main", "development")
    assert dlg is not None
    assert "Development" in dlg.windowTitle()
    texts = " | ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    assert "EQUIPO" in texts
    mono = [b.toPlainText() for b in dlg.findChildren(QPlainTextEdit)]
    assert any("version" in t and "steps" in t for t in mono), "YAML crudo presente"
    dlg.close()


def test_for_workflow_inexistente_devuelve_none():
    from desktop.widgets.entity_info_dialog import EntityInfoDialog

    _qapp()
    assert EntityInfoDialog.for_workflow(None, "main", "no_existe_12345") is None


# ── Dialog: agente ──


def test_for_agent_profile():
    from desktop.widgets.entity_info_dialog import EntityInfoDialog

    _qapp()
    profile = {
        "name": "developer",
        "type": "development",
        "system_prompt": "Eres un desarrollador senior.",
        "tools": ["file_manager", "git_manager"],
        "temperature": 0.2,
        "keywords": ["code"],
    }
    dlg = EntityInfoDialog.for_agent(None, "developer", profile)
    assert "Developer" in dlg.windowTitle()
    mono = [b.toPlainText() for b in dlg.findChildren(QPlainTextEdit)]
    assert any("desarrollador senior" in t for t in mono), "system prompt visible"
    texts = " | ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    assert "file_manager" in texts
    dlg.close()


# ── Dialog: bot ──


def test_for_bot_dict():
    from desktop.widgets.entity_info_dialog import EntityInfoDialog

    _qapp()
    bot = {
        "slug": "base",
        "display_name": "Base",
        "description": "Asistente neutral.",
        "soul_md": "SOUL: sé neutral.",
        "provider": "deepseek",
        "model": "v4-flash",
        "temperature": 0.3,
        "tool_names": ["web_search"],
        "skill_allowlist": [],
        "enabled": True,
    }
    dlg = EntityInfoDialog.for_bot(None, bot)
    assert "Base" in dlg.windowTitle()
    texts = " | ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    assert "@base" in texts
    assert "v4-flash" in texts
    mono = [b.toPlainText() for b in dlg.findChildren(QPlainTextEdit)]
    assert any("sé neutral" in t for t in mono), "SOUL visible"
    dlg.close()


# ── Botón ⓘ contextual ──


def test_info_button_label_por_modo():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()

    # Chat por defecto
    assert m._info_btn.text() == "ⓘ Agente"
    m._set_mode("orchestrate")
    assert m._info_btn.text() == "ⓘ Workflow"
    m._set_mode("chat")
    assert m._info_btn.text() == "ⓘ Agente"


def test_info_button_bot_tiene_prioridad():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._in_bot_canonical = True
    m._bot_canonical_slug = "base"
    m._update_info_button()
    assert m._info_btn.text() == "ⓘ Bot"
    # sin slug (no determinado) → cae al modo
    m._bot_canonical_slug = None
    m._update_info_button()
    assert m._info_btn.text() == "ⓘ Agente"


def test_orquestar_oculta_selector_agente():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    assert m._agent_combo.isVisibleTo(m)
    assert m._agent_label.isVisibleTo(m)
    m._set_mode("orchestrate")
    assert not m._agent_combo.isVisibleTo(m)
    assert not m._agent_label.isVisibleTo(m)
    m._set_mode("chat")
    assert m._agent_combo.isVisibleTo(m)


def test_open_info_workflow_muestra_dialog(monkeypatch):
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._set_mode("orchestrate", silent=True)
    shown: list[str] = []

    class _FakeDlg:
        def __init__(self, title, parent=None):
            self._title = title

        def show(self):
            shown.append(self._title)

        def raise_(self):
            pass

        def activateWindow(self):
            pass

    import desktop.widgets.entity_info_dialog as mod

    monkeypatch.setattr(
        mod.EntityInfoDialog,
        "for_workflow",
        classmethod(lambda cls, parent, ws, name: _FakeDlg(f"Workflow — {name}")),
    )
    m._open_info_dialog()
    assert shown and "Workflow" in shown[0]
