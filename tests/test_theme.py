"""Tests para el sistema de tokens del theme."""

import dataclasses
import re

from desktop.theme import ACCENT, COLORS, PAPEL, SURFACE, ThemeManager

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_RGBA = re.compile(r"^rgba\(\d{1,3},\s*\d{1,3},\s*\d{1,3},\s*\d{1,3}\)$")


def _iter_color_values(obj):
    if isinstance(obj, str):
        yield obj
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        yield from _iter_color_values(dataclasses.asdict(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_color_values(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_color_values(item)


def test_all_color_tokens_are_valid():
    values = list(_iter_color_values(PAPEL.colors))
    assert values, "no se iteraron colores"
    for value in values:
        assert _HEX.match(value) or _RGBA.match(value), f"color inválido: {value}"


def test_compat_colors_keep_legacy_keys():
    legacy = {
        "bg_deepest",
        "bg_near_black",
        "bg_surface",
        "bg_surface_raised",
        "bg_code",
        "bg_bash",
        "border_default",
        "border_focus",
        "border_light",
        "text_primary",
        "text_secondary",
        "text_dim",
        "text_timestamp",
        "text_timestamp_log",
        "accent",
        "accent_bright",
        "accent_hover",
        "accent_light",
        "success",
        "warning",
        "error",
        "info",
        "resume",
        "delete_btn",
        "send_btn",
        "send_btn_hover",
    }
    assert legacy <= set(COLORS)


def test_compat_aliases_follow_active_theme():
    assert ACCENT == ThemeManager.current().colors.accent_primary
    assert SURFACE == COLORS["bg_surface"]


def test_papel_tokens_intact():
    """Contrato del tema ÚNICO: papel, monocromo cálido, sin renglones."""
    assert PAPEL.name == "papel"
    assert PAPEL.colors.accent_primary == "#EDEDEF"
    assert PAPEL.colors.bg_deepest == "#131417"
    assert PAPEL.typography.family_serif.startswith("Georgia")
    assert len(PAPEL.colors.agent_palette) == 10
    assert len(PAPEL.colors.glow_spots) == 0
    assert len(PAPEL.colors.orbits) == 0
    assert PAPEL.colors.bg_to == "#131417"
    assert PAPEL.colors.rule_spacing_px == 32
    assert PAPEL.colors.accent_on == "#131417"
    assert PAPEL.colors.rule_color == "rgba(0, 0, 0, 0)"
    assert PAPEL.effects.button_gradient is False


from desktop.theme import QssFactory, StyleFactory

_FACTORY_METHODS = [
    "secondary_button",
    "accent_button",
    "group_box",
    "input_field",
    "toggle_inactive",
    "toggle_active",
    "tab_widget",
    "sidebar",
    "menu_bar",
    "progress_bar",
    "danger_button",
    "success_button",
    "tree_view",
    "list_widget",
    "text_browser_log",
    "text_browser",
    "text_editor",
    "combo_box",
    "small_button",
    "input_line",
    "detail_tabs",
    "scroll_area_chat",
    "panel",
    "primary_button",
    "ghost_button",
    "chip",
    "tooltip",
    "status_banner",
    "base_stylesheet",
]


def test_style_factory_is_singleton_instance():
    assert isinstance(StyleFactory, QssFactory)
    assert StyleFactory.theme is ThemeManager.current()


def test_all_factory_methods_return_qss():
    for name in _FACTORY_METHODS:
        method = getattr(StyleFactory, name)
        qss = method()
        assert isinstance(qss, str) and qss, f"{name} devolvió vacío"
        if name == "chip":
            assert ":" in qss, f"{name} no parece CSS"
        else:
            assert "{" in qss and "}" in qss, f"{name} no parece QSS"


def test_progress_bar_and_group_box_accept_overrides():
    qss = StyleFactory.progress_bar("#123456")
    assert "#123456" in qss
    gb = StyleFactory.group_box("#123456")
    assert "#123456" in gb


def test_base_stylesheet_contains_active_theme_tokens():
    t = ThemeManager.current()
    qss = StyleFactory.base_stylesheet()
    assert t.colors.accent_primary in qss
    assert t.colors.bg_solid in qss
    assert "QScrollBar" in qss
    assert "QToolTip" in qss


def test_input_style_uses_accent():
    from desktop.theme import INPUT_STYLE

    assert ThemeManager.current().colors.accent_primary in INPUT_STYLE


def test_toggle_active_is_inverted_ink():
    t = ThemeManager.current()
    qss = StyleFactory.toggle_active()
    assert t.colors.text_primary in qss  # fondo tinta invertida
    assert t.colors.bg_deepest in qss  # texto sobre fondo claro


def test_detail_tabs_use_underline_style():
    qss = StyleFactory.detail_tabs()
    assert "border-bottom: 2px solid" in qss
    assert "background: transparent" in qss
    assert "italic" not in qss


def test_primary_buttons_follow_button_gradient_flag():
    qss = StyleFactory.primary_button()
    if StyleFactory._e.button_gradient:
        assert "qlineargradient" in qss
    else:
        assert "qlineargradient" not in qss
        assert StyleFactory._c.accent_primary in qss
