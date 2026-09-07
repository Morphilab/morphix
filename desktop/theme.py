"""Centralized theme tokens and style factories for the Morphix desktop GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


# ============================================================
# Token groups
# ============================================================
@dataclass(frozen=True)
class GlowSpot:
    """Núcleo de luz radial del fondo (coordenadas relativas 0-1; puede salirse del rango para sangrado desde bordes)."""

    color: str
    x: float
    y: float
    radius: float
    alpha: float


@dataclass(frozen=True)
class Orbit:
    """Anillo orbital tenue del fondo."""

    color: str
    x: float
    y: float
    rx: float
    ry: float
    rotation: float
    alpha: float


@dataclass(frozen=True)
class ColorTokens:
    """Monocromo cálido — cero color de marca, jerarquía por luminosidad."""

    # Backgrounds
    bg_deepest: str = "#131417"
    bg_base: str = "#191A1E"
    bg_surface: str = "rgba(25, 26, 30, 191)"
    bg_raised: str = "rgba(35, 36, 40, 217)"
    bg_solid: str = "#1C1D21"
    bg_code: str = "#17181B"
    bg_bash: str = "#151619"
    # Accents (blancos/grises: el acento ES la luminosidad)
    accent_primary: str = "#EDEDEF"
    accent_hover: str = "#FFFFFF"
    accent_on: str = "#131417"
    accent_secondary: str = "#C9CBD1"
    accent_secondary_dark: str = "#8A8D94"
    accent_highlight: str = "#FFFFFF"
    accent_light: str = "#EDEDEF"
    # Text
    text_primary: str = "#EDEDEF"
    text_secondary: str = "#A0A3AA"
    text_dim: str = "#6B6E76"
    text_muted: str = "#54575E"
    # Borders (hairlines neutras, alfa entero 0-255)
    border_default: str = "rgba(255, 255, 255, 23)"
    border_strong: str = "rgba(255, 255, 255, 36)"
    border_accent: str = "rgba(237, 237, 239, 71)"
    border_focus: str = "rgba(237, 237, 239, 92)"
    # Semantic
    success: str = "#3FB68B"
    warning: str = "#E8B45A"
    error: str = "#E5646E"
    info: str = "#A0A3AA"
    # Workflow status
    status_completed: str = "#3FB68B"
    status_running: str = "#EDEDEF"
    status_failed: str = "#E5646E"
    status_pending: str = "#6B6E76"
    status_recovered: str = "#E8B45A"
    # Paleta de agentes (10 tonos distinguibles)
    agent_palette: tuple[str, ...] = (
        "#99A2C4",
        "#8B94BA",
        "#BCC3EC",
        "#92B489",
        "#7FA391",
        "#CBA76B",
        "#D68C83",
        "#C08AA6",
        "#A8ABB2",
        "#797C86",
    )
    # Fondo (renglones desactivados: color transparente)
    bg_to: str = "#131417"
    rule_color: str = "rgba(0, 0, 0, 0)"
    rule_spacing_px: int = 32
    glow_spots: tuple[GlowSpot, ...] = ()
    orbits: tuple[Orbit, ...] = ()


@dataclass(frozen=True)
class TypographyTokens:
    # Serif en toda la UI (la tipografía del login extendida al proyecto).
    # El mono de código/bash/tree queda en family_mono.
    family_ui: str = '"Georgia","Iowan Old Style","Times New Roman",serif'
    family_mono: str = '"JetBrains Mono","Fira Code",Consolas,monospace'
    size_xs: str = "10px"
    size_sm: str = "11px"
    size_md: str = "12px"
    size_base: str = "13px"
    size_lg: str = "14px"
    size_xl: str = "18px"
    size_title: str = "22px"
    family_serif: str = 'Georgia,"Iowan Old Style","Times New Roman",serif'


@dataclass(frozen=True)
class RadiiTokens:
    sm: str = "6px"
    md: str = "8px"
    lg: str = "12px"
    xl: str = "14px"
    pill: str = "999px"


@dataclass(frozen=True)
class EffectTokens:
    glow_enabled: bool = False
    shadow_blur: int = 24
    shadow_radius: int = 8
    shadow_color: str = "rgba(153, 162, 196, 60)"
    border_glow: bool = True
    button_gradient: bool = True  # False = botones sólidos


@dataclass(frozen=True)
class Theme:
    name: str = "papel"
    colors: ColorTokens = field(default_factory=ColorTokens)
    typography: TypographyTokens = field(default_factory=TypographyTokens)
    radii: RadiiTokens = field(default_factory=RadiiTokens)
    effects: EffectTokens = field(default_factory=EffectTokens)


# Tema ÚNICO de la GUI (tema fijo por decisión: sin palanca en runtime).
PAPEL = Theme(effects=EffectTokens(button_gradient=False))


# ============================================================
# Theme Manager
# ============================================================
class ThemeManager:
    """Entrega el tema fijo (papel) y lo aplica a la QApplication."""

    _current: Theme | None = None

    @classmethod
    def current(cls) -> Theme:
        if cls._current is None:
            cls._current = PAPEL
        return cls._current

    @classmethod
    def apply_to_app(cls, app: QApplication, theme: Theme | None = None) -> None:
        from PySide6.QtGui import QColor, QPalette  # noqa: PLC0415

        theme = theme or cls.current()
        palette = QPalette()
        for role, color in _build_dark_palette(theme).items():
            palette.setColor(QPalette.ColorGroup.All, role, QColor(color))
        app.setPalette(palette)
        app.setStyleSheet(QssFactory(theme).base_stylesheet())
        cls._current = theme


# ============================================================
# Compat aliases del dict COLORS (consumo histórico de desktop/)
# ============================================================
def _build_compat_colors(t: Theme) -> dict[str, str]:
    c = t.colors
    return {
        "bg_deepest": c.bg_deepest,
        "bg_near_black": c.bg_base,
        "bg_surface": c.bg_solid,
        "bg_surface_raised": c.bg_raised,
        "bg_code": c.bg_code,
        "bg_bash": c.bg_bash,
        "border_default": c.border_default,
        "border_focus": c.border_focus,
        "border_light": c.border_strong,
        "text_primary": c.text_primary,
        "text_secondary": c.text_secondary,
        "text_dim": c.text_dim,
        "text_timestamp": c.text_muted,
        "text_timestamp_log": c.text_muted,
        "accent": c.accent_primary,
        "accent_bright": c.accent_primary,
        "accent_hover": c.accent_hover,
        "accent_light": c.accent_light,
        "success": c.success,
        "warning": c.warning,
        "error": c.error,
        "info": c.info,
        "resume": c.success,
        "delete_btn": c.error,
        "send_btn": c.accent_primary,
        "send_btn_hover": c.accent_hover,
    }


COLORS: dict[str, str] = _build_compat_colors(PAPEL)

# Shorthand aliases for backward compatibility
ACCENT: str = COLORS["accent"]
SURFACE: str = COLORS["bg_surface"]

# Agent group colors (10-color palette, lista para iterar)
AGENT_PALETTE: list[str] = list(PAPEL.colors.agent_palette)


def _input_style(t: Theme) -> str:
    c, ty, r = t.colors, t.typography, t.radii
    # Normalización de fondos: los rgba literales del multi-tema antiguo
    # producían grises distintos por sección — superficies = bg_solid.
    return (
        f"QTextEdit {{ background: {c.bg_solid}; color: {c.text_primary}; "
        f"border: 1px solid {c.border_default}; "
        f"border-radius: {r.md}; padding: 8px; font-size: {ty.size_lg}; }}"
        f"QTextEdit:focus {{ border-color: {c.accent_primary}; }}"
    )


INPUT_STYLE: str = _input_style(PAPEL)


# ============================================================
# Qt QPalette mapping
# ============================================================
def _build_dark_palette(t: Theme | None = None) -> dict:
    """Lazy-import QPalette to avoid requiring PySide6 at module level."""
    from PySide6.QtGui import QPalette  # noqa: PLC0415

    c = (t or ThemeManager.current()).colors
    return {
        QPalette.ColorRole.Window: c.bg_deepest,
        QPalette.ColorRole.WindowText: c.text_primary,
        QPalette.ColorRole.Base: c.bg_solid,
        QPalette.ColorRole.AlternateBase: c.text_muted,
        QPalette.ColorRole.ToolTipBase: c.bg_solid,
        QPalette.ColorRole.ToolTipText: c.text_primary,
        QPalette.ColorRole.Text: c.text_primary,
        QPalette.ColorRole.Button: c.bg_solid,
        QPalette.ColorRole.ButtonText: c.text_primary,
        QPalette.ColorRole.BrightText: "#FF0000",
        QPalette.ColorRole.Link: c.accent_primary,
        QPalette.ColorRole.Highlight: c.accent_secondary_dark,
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
    }


DARK_PALETTE: dict | None = None


def get_dark_palette(t: Theme | None = None) -> dict:
    """Return the dark QPalette dict, building it on first call."""
    global DARK_PALETTE  # noqa: PLW0603
    if DARK_PALETTE is None:
        DARK_PALETTE = _build_dark_palette(t)
    return DARK_PALETTE


# ============================================================
# Style Factory (instance-based, genera QSS desde Theme)
# ============================================================
class QssFactory:
    """Genera QSS strings desde tokens. Sin dependencia de widgets."""

    def __init__(self, theme: Theme = PAPEL):
        self.theme = theme
        self._c = theme.colors
        self._ty = theme.typography
        self._r = theme.radii
        self._e = theme.effects

    def base_stylesheet(self) -> str:
        c, ty, r = self._c, self._ty, self._r
        return f"""
QWidget {{ color: {c.text_primary}; font-family: {ty.family_ui}; font-size: {ty.size_base}; }}
QPushButton {{ background: {c.bg_raised}; color: {c.text_secondary};
    border: 1px solid {c.border_default}; border-radius: {r.md};
    padding: 6px 12px; font-size: {ty.size_sm}; }}
QPushButton:hover {{ background: {c.bg_solid}; border-color: {c.border_strong}; color: {c.text_primary}; }}
QPushButton:pressed {{ background: {c.accent_hover}; }}
QPushButton:disabled {{ color: {c.text_dim}; }}
QLineEdit, QComboBox, QSpinBox {{ background: {c.bg_solid}; color: {c.text_primary};
    border: 1px solid {c.border_default}; border-radius: {r.md};
    padding: 5px 8px; font-size: {ty.size_md};
    selection-background-color: {c.accent_secondary_dark}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {c.border_focus}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c.bg_solid}; color: {c.text_primary};
    border: 1px solid {c.border_strong};
    selection-background-color: {c.accent_secondary_dark}; }}
QTextEdit, QPlainTextEdit, QTextBrowser {{ background: {c.bg_solid}; color: {c.text_primary};
    border: 1px solid {c.border_default}; border-radius: {r.md};
    selection-background-color: {c.accent_secondary_dark}; }}
QScrollArea {{ border: none; background: transparent; }}
QListWidget, QTreeView, QTableView {{ background: {c.bg_solid}; color: {c.text_primary};
    border: 1px solid {c.border_default}; border-radius: {r.md}; }}
QListWidget::item:selected, QTreeView::item:selected {{
    background: {c.accent_secondary_dark}; color: #FFFFFF; }}
QListWidget::item:hover, QTreeView::item:hover {{ background: {c.bg_raised}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c.border_strong}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c.accent_primary}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {c.border_strong}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {c.bg_raised}; color: {c.text_primary};
    border: 1px solid {c.border_accent}; border-radius: {r.sm}; padding: 4px 6px; }}
QMenuBar {{ background: {c.bg_base}; color: {c.text_secondary}; }}
QMenuBar::item:selected {{ background: {c.bg_raised}; }}
QMenu {{ background: {c.bg_solid}; color: {c.text_primary}; border: 1px solid {c.border_default}; }}
QMenu::item:selected {{ background: {c.accent_secondary_dark}; }}
QTabWidget::pane {{ border: 1px solid {c.border_default}; border-radius: {r.lg}; background: {c.bg_surface}; }}
QTabBar::tab {{ background: transparent; color: {c.text_secondary}; padding: 8px 16px;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {c.accent_primary}; border-bottom: 2px solid {c.accent_primary}; }}
QCheckBox {{ color: {c.text_secondary}; spacing: 6px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {c.border_strong}; border-radius: 3px; }}
QCheckBox::indicator:checked {{ background: {c.accent_primary}; border-color: {c.accent_primary}; }}
QProgressBar {{ background: {c.bg_raised}; border: 1px solid {c.border_default};
    border-radius: 4px; text-align: center; color: {c.text_secondary}; }}
QProgressBar::chunk {{ background: {c.accent_primary}; border-radius: 3px; }}
QStatusBar {{ background: {c.bg_base}; color: {c.text_secondary}; }}
QGroupBox {{ border: 1px solid {c.border_default}; border-radius: {r.lg};
    margin-top: 10px; padding-top: 12px; color: {c.text_primary}; }}
QGroupBox::title {{ color: {c.accent_primary}; subcontrol-origin: margin; padding: 0 6px; }}
QMainWindow {{ background: {c.bg_deepest}; }}
QDialog {{ background: {c.bg_deepest}; }}
"""

    def secondary_button(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QPushButton {{ background: {c.bg_raised}; color: {c.text_secondary}; "
            f"border: 1px solid {c.border_default}; border-radius: {r.md}; "
            f"padding: 6px 12px; font-size: {ty.size_sm}; }}"
            f"QPushButton:hover {{ background: {c.bg_solid}; border-color: {c.border_strong}; }}"
        )

    def _button_background(self) -> str:
        c = self._c
        if not self._e.button_gradient:
            return c.accent_primary
        return (
            f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {c.accent_hover}, stop:1 {c.accent_secondary_dark})"
        )

    def accent_button(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        bg = self._button_background()
        return (
            f"QPushButton {{ background: {bg}; color: {c.accent_on}; "
            f"border-radius: {r.lg}; padding: 10px; font-size: {ty.size_lg}; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {c.accent_hover}; }}"
        )

    def primary_button(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        bg = self._button_background()
        return (
            f"QPushButton {{ background: {bg}; color: {c.accent_on}; "
            f"border: 1px solid {c.border_strong}; border-radius: {r.lg}; "
            f"padding: 8px 16px; font-size: {ty.size_base}; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {c.accent_hover}; }}"
            f"QPushButton:pressed {{ background: {c.accent_secondary_dark}; }}"
            f"QPushButton:disabled {{ background: {c.bg_raised}; color: {c.text_dim}; "
            f"border: 1px solid {c.border_default}; }}"
        )

    def ghost_button(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QPushButton {{ background: transparent; color: {c.text_secondary}; "
            f"border: 1px solid {c.border_default}; border-radius: {r.md}; "
            f"padding: 6px 12px; font-size: {ty.size_sm}; }}"
            f"QPushButton:hover {{ color: {c.text_primary}; border-color: {c.border_strong}; "
            f"background: {c.bg_raised}; }}"
        )

    def danger_button(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QPushButton {{ background: {c.error}; color: #FFFFFF; "
            f"border-radius: {r.sm}; padding: 4px 8px; font-size: {ty.size_sm}; }}"
            f"QPushButton:hover {{ background: {c.error}; }}"
        )

    def success_button(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QPushButton {{ background: {c.success}; color: #FFFFFF; "
            f"border-radius: {r.sm}; padding: 4px 8px; font-size: {ty.size_sm}; }}"
            f"QPushButton:hover {{ background: {c.success}; }}"
        )

    def small_button(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QPushButton {{ background: {c.bg_raised}; color: {c.text_secondary}; "
            f"border: 1px solid {c.border_default}; border-radius: {r.sm}; "
            f"padding: 2px 8px; font-size: {ty.size_xs}; }}"
            f"QPushButton:hover {{ background: {c.border_strong}; color: {c.text_primary}; }}"
        )

    def toggle_active(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QPushButton {{ background: {c.text_primary}; color: {c.bg_deepest}; "
            f"border: 1px solid {c.border_strong}; border-radius: {r.md}; "
            f"padding: 4px 12px; font-size: {ty.size_sm}; font-weight: bold; }}"
        )

    def toggle_inactive(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QPushButton {{ background: {c.bg_solid}; color: {c.text_dim}; "
            f"border: 1px solid {c.border_strong}; border-radius: {r.md}; "
            f"padding: 4px 12px; font-size: {ty.size_sm}; }}"
            f"QPushButton:hover {{ color: {c.text_secondary}; }}"
        )

    def input_field(self) -> str:
        return INPUT_STYLE

    def input_line(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QLineEdit {{ background: {c.bg_solid}; color: {c.text_primary}; "
            f"border: 1px solid {c.border_default}; "
            f"border-radius: {r.md}; padding: 6px; font-size: {ty.size_md}; }}"
            f"QLineEdit:focus {{ border-color: {c.border_focus}; }}"
        )

    def combo_box(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QComboBox {{ background: {c.bg_raised}; color: {c.text_primary}; "
            f"border: 1px solid {c.border_default}; border-radius: {r.sm}; "
            f"padding: 2px 4px; font-size: {ty.size_xs}; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {c.bg_raised}; "
            f"color: {c.text_primary}; "
            f"selection-background-color: {c.accent_secondary_dark}; }}"
        )

    def group_box(self, title_color: str | None = None) -> str:
        c, r, ty = self._c, self._r, self._ty
        tc = title_color or c.accent_primary
        return (
            f"QGroupBox {{ background: {c.bg_surface}; border: 1px solid {c.border_default}; "
            f"border-radius: {r.lg}; margin-top: 8px; padding-top: 14px; color: {c.text_primary}; }}"
            f"QGroupBox::title {{ color: {tc}; subcontrol-origin: margin; padding: 0 6px; "
            f"font-size: {ty.size_sm}; }}"
        )

    def panel(self) -> str:
        c, r = self._c, self._r
        return (
            f"QWidget {{ background: {c.bg_surface}; border: 1px solid {c.border_default}; "
            f"border-radius: {r.xl}; }}"
        )

    def chip(self, active: bool = False) -> str:
        c, r, ty = self._c, self._r, self._ty
        border = c.border_accent if active else c.border_default
        color = c.accent_light if active else c.text_secondary
        return (
            f"background: {c.bg_surface}; border: 1px solid {border}; "
            f"border-radius: 999px; padding: 3px 10px; "
            f"font-size: {ty.size_sm}; color: {color};"
        )

    def sidebar(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QListWidget {{ background: transparent; color: {c.text_secondary}; "
            f"border: none; font-size: {ty.size_base}; padding: 8px 0px; }}"
            f"QListWidget::item {{ padding: 12px 16px; "
            f"border-left: 3px solid transparent; border-radius: 0 8px 8px 0; }}"
            f"QListWidget::item:selected {{ background: {c.bg_raised}; "
            f"color: {c.accent_light}; border-left: 3px solid {c.accent_primary}; }}"
            f"QListWidget::item:hover {{ background: {c.bg_raised}; }}"
        )

    def list_widget(self) -> str:
        c, r = self._c, self._r
        return (
            f"QListWidget {{ background: {c.bg_solid}; color: {c.text_primary}; "
            f"border: 1px solid {c.border_default}; border-radius: {r.md}; }}"
            f"QListWidget::item:selected {{ background: {c.accent_secondary_dark}; }}"
            f"QListWidget::item:hover {{ background: {c.bg_raised}; }}"
        )

    def tree_view(self) -> str:
        c, r = self._c, self._r
        return (
            f"QTreeView {{ background: {c.bg_deepest}; color: {c.text_primary}; "
            f"border: 1px solid {c.border_default}; border-radius: {r.sm}; }}"
            f"QTreeView::item:selected {{ background: {c.accent_secondary_dark}; }}"
            f"QTreeView::item:hover {{ background: {c.bg_raised}; }}"
        )

    def tab_widget(self) -> str:
        c = self._c
        return (
            f"QTabWidget::pane {{ border: 1px solid {c.border_default}; "
            f"background: {c.bg_deepest}; }}"
            f"QTabBar::tab {{ background: transparent; color: {c.text_secondary}; "
            f"padding: 8px 16px; border: none; "
            f"border-bottom: 2px solid transparent; }}"
            f"QTabBar::tab:selected {{ color: {c.accent_primary}; "
            f"border-bottom: 2px solid {c.accent_primary}; }}"
        )

    def editor_tabs(self) -> str:
        """Pestañas compactas de documentos (pestaña Editor) — con botón cerrar."""
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QTabWidget::pane {{ border: 1px solid {c.border_default}; "
            f"background: {c.bg_deepest}; border-radius: {r.sm}; }}"
            f"QTabBar::tab {{ background: {c.bg_solid}; color: {c.text_secondary}; "
            f"padding: 4px 10px; margin-right: 2px; "
            f"border: 1px solid {c.border_default}; border-bottom: none; "
            f"border-top-left-radius: {r.sm}; border-top-right-radius: {r.sm}; "
            f"font-family: {ty.family_ui}; font-size: {ty.size_sm}; }}"
            f"QTabBar::tab:selected {{ background: {c.bg_deepest}; "
            f"color: {c.accent_primary}; border-color: {c.border_strong}; }}"
            f"QTabBar::tab:hover {{ color: {c.text_primary}; }}"
        )

    def breadcrumb_button(self) -> str:
        """Migas de navegación del Editor — segmento clicable plano."""
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QPushButton {{ background: transparent; color: {c.text_secondary}; "
            f"border: none; border-radius: {r.sm}; padding: 2px 6px; "
            f"font-family: {ty.family_ui}; font-size: {ty.size_xs}; }}"
            f"QPushButton:hover {{ color: {c.accent_primary}; background: {c.bg_raised}; }}"
        )

    def detail_tabs(self) -> str:
        c, ty = self._c, self._ty
        return (
            f"QTabWidget::pane {{ border: 1px solid {c.border_default}; "
            f"border-radius: 12px; }}"
            f"QTabBar::tab {{ background: transparent; color: {c.text_dim}; "
            f"padding: 8px 2px; margin-right: 18px; border: none; "
            f"border-bottom: 2px solid transparent; "
            f"font-family: {ty.family_ui}; font-size: {ty.size_md}; }}"
            f"QTabBar::tab:selected {{ color: {c.accent_secondary}; "
            f"border-bottom: 2px solid {c.accent_secondary_dark}; font-weight: 600; }}"
        )

    def menu_bar(self) -> str:
        c = self._c
        return (
            f"QMenuBar {{ background: {c.bg_base}; color: {c.text_secondary}; }}"
            f"QMenuBar::item:selected {{ background: {c.bg_raised}; }}"
            f"QMenu {{ background: {c.bg_solid}; color: {c.text_primary}; "
            f"border: 1px solid {c.border_default}; }}"
            f"QMenu::item:selected {{ background: {c.accent_secondary_dark}; }}"
        )

    def progress_bar(self, color: str | None = None) -> str:
        c = self._c
        chunk = (
            f"qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {c.accent_secondary_dark}, stop:1 {c.accent_primary})"
            if color is None
            else color
        )
        return (
            f"QProgressBar {{ background: {c.border_default}; border: none; "
            f"border-radius: 3px; height: 6px; text-align: center; "
            f"color: {c.text_dim}; }}"
            f"QProgressBar::chunk {{ background: {chunk}; border-radius: 3px; }}"
        )

    def text_browser_log(self) -> str:
        c, ty = self._c, self._ty
        # Regla de 2 tonos: el log es superficie → bg_solid (el mono font ya
        # lo distingue; fuera el tercer tono bg_code del Maestro).
        return (
            f"QTextBrowser {{ background: {c.bg_solid}; color: {c.text_primary}; "
            f"border: 1px solid {c.border_default}; border-radius: {self._r.sm}; "
            f"font-family: {ty.family_mono}; font-size: {ty.size_xs}; }}"
        )

    def text_browser(self) -> str:
        c, r, ty = self._c, self._r, self._ty
        return (
            f"QTextBrowser {{ background: {c.bg_solid}; color: {c.text_primary}; "
            f"border: 1px solid {c.border_default}; border-radius: {r.md}; "
            f"padding: 8px; font-family: {ty.family_ui}; font-size: {ty.size_base}; }}"
        )

    def text_editor(self) -> str:
        c, ty = self._c, self._ty
        return (
            f"QPlainTextEdit {{ background: {c.bg_solid}; color: {c.text_primary}; "
            f"border: 1px solid {c.border_default}; border-radius: {self._r.sm}; "
            f"font-family: {ty.family_mono}; font-size: {ty.size_base}; }}"
        )

    def scroll_area_chat(self) -> str:
        c = self._c
        # Regla de 2 tonos: el canvas del chat es chrome → bg_deepest.
        return (
            f"QScrollArea {{ background: {c.bg_deepest}; "
            f"border: 1px solid {c.border_default}; border-radius: {self._r.lg}; }}"
        )

    def tooltip(self) -> str:
        c, r = self._c, self._r
        return (
            f"QToolTip {{ background: {c.bg_raised}; color: {c.text_primary}; "
            f"border: 1px solid {c.border_accent}; border-radius: {r.sm}; padding: 4px 6px; }}"
        )

    def status_banner(self, kind: str = "info") -> str:
        c, r, ty = self._c, self._r, self._ty
        colors = {"info": c.info, "error": c.error, "warning": c.warning}
        col = colors.get(kind, c.info)
        return (
            f"QLabel {{ background: {col}; color: #FFFFFF; border-radius: {r.md}; "
            f"padding: 6px 10px; font-size: {ty.size_md}; }}"
        )


# Singleton para backward-compat: StyleFactory.xxx() sigue funcionando
StyleFactory = QssFactory(PAPEL)
