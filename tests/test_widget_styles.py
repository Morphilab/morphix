"""Verifica que los widgets migrados usan tokens del theme (sin hex legacy)."""

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.theme import COLORS, ThemeManager  # noqa: E402

_LEGACY_HEX = ["#1A1A1A", "#2A2A2A", "#E5E5E5", "#A0A0A0", "#0F0F0F", "#888888", "#555"]


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def _assert_no_legacy_hex(text: str):
    for hex_val in _LEGACY_HEX:
        assert hex_val not in text, f"hex legacy presente: {hex_val}"


def test_stat_chips_use_tokens():
    _qapp()
    from desktop.widgets.stat_chips import CHIP_STYLE

    assert ThemeManager.current().colors.bg_surface in CHIP_STYLE
    _assert_no_legacy_hex(CHIP_STYLE)


def test_collapsible_section_button_uses_tokens():
    _qapp()
    c = ThemeManager.current().colors
    from desktop.widgets.collapsible_section import CollapsibleSection

    section = CollapsibleSection("Test")
    style = section._toggle_btn.styleSheet()
    assert c.text_secondary in style
    assert "border-bottom: 2px solid" in style
    assert c.accent_secondary_dark in style
    _assert_no_legacy_hex(style)


def test_bash_panel_uses_tokens():
    _qapp()
    from desktop.widgets.bash_panel import BashPanel

    panel = BashPanel()
    style = panel.output.styleSheet()
    assert ThemeManager.current().colors.bg_bash in style
    assert ThemeManager.current().colors.success in style
    _assert_no_legacy_hex(style)


def test_chat_bubble_role_colors():
    from desktop.widgets.chat_bubble import _ROLE_CONFIG

    assert _ROLE_CONFIG["user"] == ("Tú", ThemeManager.current().colors.text_secondary)
    assert _ROLE_CONFIG["assistant"] == ("Morphix", ThemeManager.current().colors.accent_secondary)


def test_chat_block_paints_dashed_rule():
    _qapp()
    from desktop.widgets.chat_bubble import ChatBlock

    class NoRuleBlock(ChatBlock):
        def paintEvent(self, event):  # noqa: N802
            from PySide6.QtWidgets import QWidget

            QWidget.paintEvent(self, event)

    real = ChatBlock("hola", "assistant")
    control = NoRuleBlock("hola", "assistant")
    for b in (real, control):
        b.resize(300, 80)
        b.repaint()
    img_real = real.grab().toImage()
    img_ctrl = control.grab().toImage()
    h = min(real.height(), control.height())
    differing = sum(
        1
        for x in range(8, 100)
        for y in (h - 2, h - 1)
        if img_real.pixelColor(x, y) != img_ctrl.pixelColor(x, y)
    )
    assert differing >= 10


def test_execution_panel_list_style_uses_tokens():
    from desktop.panels.execution_panel import LIST_STYLE

    assert COLORS["bg_surface"] in LIST_STYLE
    _assert_no_legacy_hex(LIST_STYLE)


def test_phase_cards_inject_theme_colors():
    _qapp()
    from desktop.widgets.phase_cards import PhaseCards

    cards = PhaseCards()
    cards.update_from_stats(
        {"subtask_list": [{"name": "Implementar", "status": "running"}], "phase": "build"}
    )
    html = cards.toHtml()
    assert ThemeManager.current().colors.status_running.lower() in html.lower()
    assert "transparent" in html.lower()


def test_stat_chips_running_branch_and_escaping():
    _qapp()
    from desktop.widgets.stat_chips import StatChips

    chips = StatChips()
    chips.update_from_stats({"status": "Executing DAG", "current_agent": "<b>x</b>"})
    status_html = chips._labels["status"].text()
    agent_html = chips._labels["current_agent"].text()
    assert (
        ThemeManager.current().colors.status_running in status_html
    )  # rama activa con estados en inglés
    assert "&lt;b&gt;" in agent_html  # interpolación escapada
