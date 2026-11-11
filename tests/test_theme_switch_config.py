"""Switch de tema (Config → Apariencia): persistencia en .env + contrato del toggle.

El tema se resuelve al arrancar; el toggle persiste DARK_MODE en .env y la
aplicación efectiva es al reiniciar (aviso honesto en la UI).
"""

import os
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.config import settings  # noqa: E402


def test_set_dark_mode_persists_env(tmp_path, monkeypatch):
    import core.config as cfg
    from desktop.services.config_service import ConfigService

    env = tmp_path / ".env"
    env.write_text("ACTIVE_WORKSPACE=main\nDARK_MODE=true\nOFFLINE_MODE=false\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "ENV_PATH", env)
    monkeypatch.setattr(settings, "dark_mode", True, raising=False)

    result = ConfigService.set_dark_mode(False)

    assert result == {"success": True, "dark_mode": False}
    assert settings.dark_mode is False
    text = env.read_text(encoding="utf-8")
    assert "DARK_MODE=false" in text
    assert "DARK_MODE=true" not in text
    assert "ACTIVE_WORKSPACE=main" in text
    assert "OFFLINE_MODE=false" in text


def test_set_dark_mode_appends_when_absent(tmp_path, monkeypatch):
    import core.config as cfg
    from desktop.services.config_service import ConfigService

    env = tmp_path / ".env"
    env.write_text("OFFLINE_MODE=false\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "ENV_PATH", env)
    monkeypatch.setattr(settings, "dark_mode", True, raising=False)

    ConfigService.set_dark_mode(False)

    assert "DARK_MODE=false" in env.read_text(encoding="utf-8")


@pytest.fixture()
def _qt():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return cast(QApplication, QApplication.instance() or QApplication([]))


def _tab(monkeypatch):
    from desktop import config_tab as ct

    async def _noop(self):
        return None

    monkeypatch.setattr(ct.ConfigTab, "_refresh", _noop)
    return ct.ConfigTab()


def test_config_tab_has_appearance_tab(_qt, monkeypatch):
    tab = _tab(monkeypatch)
    labels = [tab.inner_tabs.tabText(i) for i in range(tab.inner_tabs.count())]
    assert "Apariencia" in labels


def test_appearance_toggle_marks_restart_and_flips_buttons(_qt, monkeypatch):
    from desktop.services import config_service as cs
    from desktop.theme import StyleFactory

    monkeypatch.setattr(
        cs.ConfigService,
        "set_dark_mode",
        staticmethod(lambda enabled: {"success": True, "dark_mode": enabled}),
    )

    tab = _tab(monkeypatch)
    tab._set_theme(False)
    assert "claro" in tab.theme_status.text()
    assert "reiniciar" in tab.theme_status.text()
    assert tab.theme_light_btn.styleSheet() == StyleFactory.toggle_active()

    tab._set_theme(True)
    assert "oscuro" in tab.theme_status.text()
    assert tab.theme_dark_btn.styleSheet() == StyleFactory.toggle_active()
