"""Tests del tema claro (papel claro) — contrato, contraste WCAG y guards.

Cubre: tokens del tema claro, contraste de la escalera de texto y semánticos en
AMBOS temas (la jerarquía percibida debe espejarse), paleta de agentes en hex de
6 (debate_section compone `{color}44` por concatenación), el parser qcolor
compartido y el guard de que QssFactory no reintroduce literales de color.
"""

import inspect
import re

import pytest

from desktop.theme import PAPEL, PAPEL_CLARO, QssFactory, qcolor


def _lum(color: str) -> float:
    """Luminancia relativa WCAG de un hex #RRGGBB."""
    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_papel_claro_tokens():
    c = PAPEL_CLARO.colors
    assert PAPEL_CLARO.name == "papel_claro"
    assert c.bg_deepest == "#FFFFFF"
    assert c.bg_solid == "#FFFFFF"
    assert c.text_primary == "#000000"
    assert c.accent_primary == "#000000"
    assert c.accent_on == "#FFFFFF"
    assert c.selection_bg == "#D6D8DB"
    assert c.selection_text == "#000000"
    assert c.border_default == "rgba(0, 0, 0, 46)"
    assert c.success == "#0B6B45"
    assert c.warning == "#7A520A"
    assert c.error == "#A81E2B"
    assert len(c.agent_palette) == 10
    assert len(c.glow_spots) == 0
    assert len(c.orbits) == 0
    assert PAPEL_CLARO.effects.button_gradient is False


@pytest.mark.parametrize("theme", [PAPEL, PAPEL_CLARO], ids=["papel", "papel_claro"])
def test_text_ramp_contrast(theme):
    c = theme.colors
    assert _ratio(c.text_primary, c.bg_solid) >= 12.0
    assert _ratio(c.text_secondary, c.bg_solid) >= 4.5
    assert _ratio(c.text_dim, c.bg_solid) >= 3.0
    assert _ratio(c.text_primary, c.bg_deepest) >= 12.0


@pytest.mark.parametrize("theme", [PAPEL, PAPEL_CLARO], ids=["papel", "papel_claro"])
def test_semantic_contrast(theme):
    c = theme.colors
    for color in (c.success, c.warning, c.error, c.info, c.status_running, c.status_failed):
        assert _ratio(color, c.bg_solid) >= 4.5, f"{color} bajo 4.5:1 sobre bg_solid"


@pytest.mark.parametrize("theme", [PAPEL, PAPEL_CLARO], ids=["papel", "papel_claro"])
def test_agent_palette_hex6_and_contrast(theme):
    c = theme.colors
    for color in c.agent_palette:
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", color), f"no hex-6: {color}"
        assert _ratio(color, c.bg_solid) >= 4.0, f"{color} ilegible sobre bg_solid"


def test_papel_claro_semantic_on_contrast():
    """Texto sobre banners/botones semánticos del claro (el oscuro conserva su
    diseño histórico: blanco sobre semánticos)."""
    c = PAPEL_CLARO.colors
    for color in (c.success, c.warning, c.error, c.info):
        assert _ratio(c.semantic_on, color) >= 4.5


def test_qcolor_parses_hex_and_rgba():
    assert qcolor("#FFFFFF").name() == "#ffffff"
    translucent = qcolor("rgba(0, 0, 0, 102)")
    assert (
        translucent.red(),
        translucent.green(),
        translucent.blue(),
        translucent.alpha(),
    ) == (0, 0, 0, 102)
    frac = qcolor("rgba(255, 255, 255, 0.5)")
    assert frac.alpha() == 128


def test_qss_factory_has_no_color_literals():
    """Guard: todo color del QSS sale de tokens — un literal rompe un tema."""
    src = inspect.getsource(QssFactory)
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b", src)


def test_claro_qss_uses_its_tokens():
    qss = QssFactory(PAPEL_CLARO).base_stylesheet()
    c = PAPEL_CLARO.colors
    for token in (c.selection_bg, c.selection_text, c.border_default, c.text_primary):
        assert token in qss
    assert qss != QssFactory(PAPEL).base_stylesheet()


def test_input_field_follows_its_theme():
    """Regresión: input_field() devolvía el snapshot del tema activo e ignoraba
    el tema de la factory (latente hasta que existió un segundo tema)."""
    light = QssFactory(PAPEL_CLARO).input_field()
    dark = QssFactory(PAPEL).input_field()
    assert PAPEL_CLARO.colors.bg_solid in light
    assert PAPEL.colors.bg_solid in dark
    assert light != dark
