"""Tests del tema ÚNICO (papel, monocromo) y del manager sin dispatch."""

from desktop.theme import PAPEL, ThemeManager


def test_papel_tokens_are_monochrome():
    c = PAPEL.colors
    assert c.bg_deepest == "#131417"
    assert c.bg_base == "#191A1E"
    assert c.bg_solid == "#1C1D21"
    assert c.bg_raised == "rgba(35, 36, 40, 217)"
    assert c.accent_primary == "#EDEDEF"
    assert c.accent_on == "#131417"
    assert c.text_primary == "#EDEDEF"
    assert c.text_secondary == "#A0A3AA"
    assert c.border_default == "rgba(255, 255, 255, 23)"
    assert c.success == "#3FB68B"
    assert c.warning == "#E8B45A"
    assert c.error == "#E5646E"
    assert c.info == "#A0A3AA"  # neutro: monocromo
    assert c.rule_color == "rgba(0, 0, 0, 0)"  # sin renglones


def test_agent_palette_and_no_glow():
    assert len(PAPEL.colors.agent_palette) == 10
    assert len(PAPEL.colors.glow_spots) == 0
    assert len(PAPEL.colors.orbits) == 0


def test_theme_is_single_and_fixed():
    """Sin dispatch ni settings: current() SIEMPRE es PAPEL."""
    assert ThemeManager.current() is PAPEL
    assert not hasattr(ThemeManager, "theme_from_name")


def test_papel_primary_button_is_solid():
    from desktop.theme import QssFactory

    qss = QssFactory(PAPEL).primary_button()
    assert "qlineargradient" not in qss
    assert PAPEL.colors.accent_primary in qss
    assert PAPEL.colors.accent_on in qss


def test_papel_detail_tabs_have_no_italic_serif():
    from desktop.theme import QssFactory

    qss = QssFactory(PAPEL).detail_tabs()
    assert "italic" not in qss
    assert "font-family: Georgia" not in qss


def test_single_theme_no_legacy_names_in_module():
    """Guard del tema fijo: en el módulo no quedan nombres del multi-tema
    (bruma/dispatch) ni el campo de config que los alimentaba."""
    import inspect

    import desktop.theme as theme_mod
    from core.config import Settings

    source = inspect.getsource(theme_mod)
    for forbidden in ("BRUMA", "ORBITAL", "theme_from_name", "gui_theme"):
        assert forbidden not in source, f"residuo de multi-tema: {forbidden}"
    assert "gui_theme" not in Settings.model_fields
