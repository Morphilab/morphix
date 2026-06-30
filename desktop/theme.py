"""Centralized theme tokens and style factories for the Morphix desktop GUI."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

# ============================================================
# Color Tokens
# ============================================================
COLORS: dict[str, str] = {
    # Backgrounds (darkest -> lightest)
    "bg_deepest": "#0F0F0F",
    "bg_near_black": "#141414",
    "bg_surface": "#1A1A1A",
    "bg_surface_raised": "#1E293B",
    "bg_code": "#0F172A",
    "bg_bash": "#0A0A0A",
    # Borders & dividers
    "border_default": "#2A2A2A",
    "border_focus": "#1066ae",
    "border_light": "#334155",
    # Text
    "text_primary": "#E5E5E5",
    "text_secondary": "#A0A0A0",
    "text_dim": "#888888",
    "text_timestamp": "#666666",
    "text_timestamp_log": "#555555",
    # Accent (blue palette)
    "accent": "#1066ae",
    "accent_bright": "#2563EB",
    "accent_hover": "#1a80d0",
    "accent_light": "#60A5FA",
    # Semantic
    "success": "#22C55E",
    "warning": "#F59E0B",
    "error": "#EF4444",
    "info": "#3B82F6",
    # Special buttons
    "resume": "#16A34A",
    "delete_btn": "#EF4444",
    "send_btn": "#1066ae",
    "send_btn_hover": "#1a80d0",
}

# Shorthand aliases for backward compatibility
ACCENT: str = COLORS["accent"]
SURFACE: str = COLORS["bg_surface"]
INPUT_STYLE: str = (
    f"QTextEdit {{ background: {SURFACE}; color: {COLORS['text_primary']}; "
    f"border: 1px solid {COLORS['border_default']}; "
    f"border-radius: 8px; padding: 8px; font-size: 14px; }}"
    f"QTextEdit:focus {{ border-color: {ACCENT}; }}"
)

# Agent group colors (10-color palette)
AGENT_PALETTE: list[str] = [
    "#F59E0B",
    "#22C55E",
    "#3B82F6",
    "#A855F7",
    "#EF4444",
    "#EC4899",
    "#14B8A6",
    "#F97316",
    "#06B6D4",
    "#8B5CF6",
]


# ============================================================
# Qt QPalette mapping
# ============================================================
def _build_dark_palette() -> dict:
    """Lazy-import QPalette to avoid requiring PySide6 at module level."""
    from PySide6.QtGui import QPalette  # noqa: PLC0415

    return {
        QPalette.ColorRole.Window: COLORS["bg_deepest"],
        QPalette.ColorRole.WindowText: COLORS["text_primary"],
        QPalette.ColorRole.Base: COLORS["bg_surface"],
        QPalette.ColorRole.AlternateBase: COLORS["border_default"],
        QPalette.ColorRole.ToolTipBase: COLORS["bg_surface"],
        QPalette.ColorRole.ToolTipText: COLORS["text_primary"],
        QPalette.ColorRole.Text: COLORS["text_primary"],
        QPalette.ColorRole.Button: COLORS["bg_surface"],
        QPalette.ColorRole.ButtonText: COLORS["text_primary"],
        QPalette.ColorRole.BrightText: "#FF0000",
        QPalette.ColorRole.Link: COLORS["accent"],
        QPalette.ColorRole.Highlight: COLORS["accent"],
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
    }


DARK_PALETTE: dict | None = None


def get_dark_palette() -> dict:
    """Return the dark QPalette dict, building it on first call."""
    global DARK_PALETTE  # noqa: PLW0603
    if DARK_PALETTE is None:
        DARK_PALETTE = _build_dark_palette()
    return DARK_PALETTE


# ============================================================
# Style Factories (return QSS strings)
# ============================================================
class StyleFactory:
    """Generates QSS style strings from tokens. No widget dependency."""

    _ACCENT: str = COLORS["accent"]
    _SURFACE: str = COLORS["bg_surface"]
    _BORDER: str = COLORS["border_default"]
    _TEXT: str = COLORS["text_primary"]
    _SECONDARY: str = COLORS["text_secondary"]

    @classmethod
    def secondary_button(cls) -> str:
        return (
            f"QPushButton {{ background: {cls._SURFACE}; color: {cls._SECONDARY}; "
            f"border: 1px solid {cls._BORDER}; border-radius: 6px; "
            f"padding: 6px 12px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {cls._BORDER}; }}"
        )

    @classmethod
    def accent_button(cls) -> str:
        return (
            f"QPushButton {{ background: {cls._ACCENT}; color: {COLORS['bg_deepest']}; "
            f"border-radius: 8px; padding: 10px; font-size: 14px; "
            f"font-weight: bold; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_hover']}; }}"
        )

    @classmethod
    def group_box(cls, title_color: str | None = None) -> str:
        tc = title_color or cls._ACCENT
        return (
            f"QGroupBox {{ border: 1px solid {cls._BORDER}; border-radius: 8px; "
            f"margin-top: 8px; padding-top: 14px; color: {cls._TEXT}; }}"
            f"QGroupBox::title {{ color: {tc}; subcontrol-origin: margin; "
            f"padding: 0 6px; }}"
        )

    @classmethod
    def input_field(cls) -> str:
        return INPUT_STYLE

    @classmethod
    def input_field_focused(cls) -> str:
        return (
            f"QTextEdit {{ background: {cls._SURFACE}; color: {cls._TEXT}; "
            f"border: 1px solid {cls._ACCENT}; border-radius: 8px; "
            f"padding: 8px; font-size: 14px; }}"
        )

    @classmethod
    def toggle_inactive(cls) -> str:
        return (
            f"QPushButton {{ background: {COLORS['bg_surface_raised']}; "
            f"color: {cls._SECONDARY}; border: 1px solid {cls._BORDER}; "
            f"border-radius: 6px; padding: 4px 10px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {cls._BORDER}; }}"
        )

    @classmethod
    def toggle_active(cls) -> str:
        return (
            f"QPushButton {{ background: {COLORS['accent_bright']}; color: #FFFFFF; "
            f"border: none; border-radius: 6px; padding: 4px 10px; "
            f"font-size: 11px; font-weight: bold; }}"
        )

    @classmethod
    def tab_widget(cls) -> str:
        return (
            f"QTabWidget::pane {{ border: 1px solid {cls._BORDER}; "
            f"background: {COLORS['bg_deepest']}; }}"
            f"QTabBar::tab {{ background: {cls._SURFACE}; color: {cls._SECONDARY}; "
            f"padding: 8px 16px; border: none; "
            f"border-bottom: 2px solid transparent; }}"
            f"QTabBar::tab:selected {{ color: {cls._ACCENT}; "
            f"border-bottom: 2px solid {cls._ACCENT}; }}"
        )

    @classmethod
    def sidebar(cls) -> str:
        return (
            f"QListWidget {{ background: {cls._SURFACE}; color: {cls._SECONDARY}; "
            f"border: none; font-size: 14px; padding: 8px 0px; }}"
            f"QListWidget::item {{ padding: 12px 16px; "
            f"border-left: 3px solid transparent; }}"
            f"QListWidget::item:selected {{ background: {COLORS['bg_surface_raised']}; "
            f"color: {cls._ACCENT}; border-left: 3px solid {cls._ACCENT}; }}"
            f"QListWidget::item:hover {{ background: {cls._BORDER}; }}"
        )

    @classmethod
    def menu_bar(cls) -> str:
        return (
            f"QMenuBar {{ background: {cls._SURFACE}; color: {cls._SECONDARY}; }}"
            f"QMenuBar::item:selected {{ background: {cls._BORDER}; }}"
            f"QMenu {{ background: {cls._SURFACE}; color: {cls._TEXT}; "
            f"border: 1px solid {cls._BORDER}; }}"
            f"QMenu::item:selected {{ background: {cls._ACCENT}; }}"
        )

    @classmethod
    def status_bar(cls) -> str:
        return f"QStatusBar {{ background: {cls._SURFACE}; color: {cls._SECONDARY}; }}"

    @classmethod
    def progress_bar(cls, color: str | None = None) -> str:
        c = color or COLORS["accent_bright"]
        return (
            f"QProgressBar {{ background: {COLORS['bg_surface_raised']}; "
            f"border: 1px solid {COLORS['border_light']}; "
            f"border-radius: 4px; height: 8px; text-align: center; }}"
            f"QProgressBar::chunk {{ background: {c}; border-radius: 3px; }}"
        )

    @classmethod
    def card_button(cls) -> str:
        return (
            f"QPushButton {{ background: {cls._SURFACE}; color: {cls._ACCENT}; "
            f"border: 1px solid {cls._BORDER}; border-radius: 6px; "
            f"padding: 6px 10px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {cls._ACCENT}; color: {cls._TEXT}; }}"
        )

    @classmethod
    def danger_button(cls) -> str:
        return (
            f"QPushButton {{ background: {COLORS['delete_btn']}; color: #FFFFFF; "
            f"border-radius: 4px; padding: 4px 8px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #CC3333; }}"
        )

    @classmethod
    def success_button(cls) -> str:
        return (
            f"QPushButton {{ background: {COLORS['resume']}; color: #FFFFFF; "
            f"border-radius: 4px; padding: 4px 8px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: #138A3A; }}"
        )

    @classmethod
    def tree_view(cls) -> str:
        return (
            f"QTreeView {{ background: {COLORS['bg_deepest']}; "
            f"color: {cls._TEXT}; border: 1px solid {cls._BORDER}; "
            f"border-radius: 4px; }}"
            f"QTreeView::item:selected {{ background: {cls._ACCENT}; }}"
            f"QTreeView::item:hover {{ background: {cls._BORDER}; }}"
        )

    @classmethod
    def list_widget(cls) -> str:
        return (
            f"QListWidget {{ background: {cls._SURFACE}; "
            f"color: {cls._TEXT}; border: 1px solid {cls._BORDER}; "
            f"border-radius: 4px; }}"
            f"QListWidget::item:selected {{ background: {cls._ACCENT}; }}"
            f"QListWidget::item:hover {{ background: {cls._BORDER}; }}"
        )

    @classmethod
    def text_browser_log(cls) -> str:
        return (
            f"QTextBrowser {{ background: {COLORS['bg_deepest']}; "
            f"color: {cls._TEXT}; border: 1px solid {cls._BORDER}; "
            f"border-radius: 4px; font-family: monospace; font-size: 10px; }}"
        )

    @classmethod
    def text_editor(cls) -> str:
        return (
            f"QPlainTextEdit {{ background: {cls._SURFACE}; "
            f"color: {cls._TEXT}; border: 1px solid {cls._BORDER}; "
            f"border-radius: 4px; font-family: 'JetBrains Mono', 'Fira Code', "
            f"'Cascadia Code', monospace; font-size: 13px; }}"
        )

    @classmethod
    def combo_box(cls) -> str:
        return (
            f"QComboBox {{ background: {COLORS['bg_surface_raised']}; "
            f"color: {cls._SECONDARY}; border: 1px solid {cls._BORDER}; "
            f"border-radius: 4px; padding: 2px 4px; font-size: 10px; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {COLORS['bg_surface_raised']}; "
            f"color: {cls._SECONDARY}; "
            f"selection-background-color: {COLORS['accent_bright']}; }}"
        )

    @classmethod
    def small_button(cls) -> str:
        return (
            f"QPushButton {{ background: {COLORS['bg_surface_raised']}; "
            f"color: {cls._SECONDARY}; border: 1px solid {cls._BORDER}; "
            f"border-radius: 4px; padding: 2px 8px; font-size: 10px; }}"
            f"QPushButton:hover {{ background: {cls._BORDER}; color: {cls._TEXT}; }}"
        )

    @classmethod
    def input_line(cls) -> str:
        return (
            f"QLineEdit {{ background: {cls._SURFACE}; color: {cls._SECONDARY}; "
            f"border: 1px solid {cls._BORDER}; "
            f"border-radius: 6px; padding: 6px; font-size: 12px; }}"
        )

    @classmethod
    def detail_tabs(cls) -> str:
        return (
            f"QTabWidget::pane {{ border: 1px solid {cls._BORDER}; border-radius: 8px; }}"
            f"QTabBar::tab {{ background: {cls._SURFACE}; color: {cls._SECONDARY}; "
            f"padding: 6px 10px; border: 1px solid {cls._BORDER}; border-bottom: none; "
            f"border-top-left-radius: 6px; border-top-right-radius: 6px; font-size: 11px; }}"
            f"QTabBar::tab:selected {{ background: {COLORS['accent_bright']}; "
            f"color: #FFFFFF; }}"
        )

    @classmethod
    def scroll_area_chat(cls) -> str:
        return (
            f"QScrollArea {{ background: {COLORS['bg_deepest']}; "
            f"border: 1px solid {cls._BORDER}; border-radius: 8px; }}"
        )

    @classmethod
    def home_accent_button(cls) -> str:
        return (
            f"QPushButton {{ background: {cls._ACCENT}; color: #FFFFFF; "
            f"border-radius: 6px; padding: 6px 12px; font-size: 11px; }}"
        )


# ============================================================
# Theme Manager
# ============================================================
class ThemeManager:
    """Applies/detects theme (extensible for future light/dark)."""

    _current: str = "dark"

    @classmethod
    def current(cls) -> str:
        return cls._current

    @classmethod
    def apply_to_app(cls, app: QApplication) -> None:
        from PySide6.QtGui import QColor, QPalette  # noqa: PLC0415

        palette = QPalette()
        for role, color in get_dark_palette().items():
            palette.setColor(QPalette.ColorGroup.All, role, QColor(color))
        app.setPalette(palette)
        app.setStyleSheet(
            StyleFactory.tab_widget() + StyleFactory.menu_bar() + StyleFactory.status_bar()
        )
