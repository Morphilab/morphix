"""Iconos SVG de línea 16px — sustituyen a los emojis del sidebar (spec §3).

Uso: get_icon("maestro") → QIcon. Paths stroke-based (1.5px) con color
inyectado; cache por (name, color).
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_STROKE = (
    'fill="none" stroke="COLOR" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
)

_PATHS: dict[str, str] = {
    "dashboard": '<rect x="2" y="2" width="5" height="5" rx="1"/><rect x="9" y="2" width="5" height="5" rx="1"/><rect x="2" y="9" width="5" height="5" rx="1"/><rect x="9" y="9" width="5" height="5" rx="1"/>',
    "maestro": '<path d="M4 5.5h12M4 8h12M4 10.5h8"/><path d="M14 13l2.5-2.5"/>',
    "historial": '<circle cx="8" cy="8" r="6"/><path d="M8 5v3.5l2.5 1.5"/>',
    "editor": '<path d="M11.5 3.5l1 1L6 11l-1.5.5L5 10l6.5-6.5z"/><path d="M3 14h10"/>',
    "config": '<circle cx="8" cy="8" r="2.2"/><path d="M8 2v2M8 12v2M2 8h2M12 8h2M3.8 3.8l1.4 1.4M10.8 10.8l1.4 1.4M12.2 3.8l-1.4 1.4M5.2 10.8l-1.4 1.4"/>',
    "analytics": '<path d="M2.5 13.5h11"/><rect x="4" y="7" width="2.2" height="6.5" rx="0.5"/><rect x="7.5" y="4" width="2.2" height="9.5" rx="0.5"/><rect x="11" y="9" width="2.2" height="4.5" rx="0.5"/>',
    "memoria": '<ellipse cx="8" cy="4" rx="5" ry="2"/><path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4"/><path d="M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/>',
    "bots": '<rect x="3" y="6" width="10" height="7" rx="1.5"/><path d="M8 6V3.2"/><circle cx="8" cy="2.4" r="0.9"/><circle cx="6.2" cy="9.5" r="0.5"/><circle cx="9.8" cy="9.5" r="0.5"/><path d="M5.5 11.5h5"/>',
    "attach": '<path d="M11 5l-4.5 4.5a1.8 1.8 0 002.5 2.5L13.5 7.5a3 3 0 00-4.2-4.2L4.8 7.8"/>',
    "download": '<path d="M8 3v7M5.5 7.5L8 10l2.5-2.5"/><path d="M3.5 12.5h9"/>',
    "plus": '<path d="M8 4v8M4 8h8"/>',
    "refresh": '<path d="M13 8a5 5 0 11-1.5-3.5"/><path d="M13 2.5V5h-2.5"/>',
    "folder": '<path d="M2.5 4.5h4l1.2 1.5h5.8v6a1 1 0 01-1 1h-9a1 1 0 01-1-1v-7.5z"/>',
}

AVAILABLE_ICONS = frozenset(_PATHS)
_cache: dict[tuple[str, str], QIcon] = {}


def get_icon(name: str, color: str = "#A0A3AA", size: int = 16) -> QIcon:
    key = (name, color)
    if key in _cache:
        return _cache[key]
    path = _PATHS.get(name, _PATHS["dashboard"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        f'<g {_STROKE.replace("COLOR", color)}>{path}</g></svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    icon = QIcon(pixmap)
    _cache[key] = icon
    return icon
