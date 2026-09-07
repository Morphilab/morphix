"""OrbitalBackground — fondo de la GUI: lienzo sólido con renglones opcionales.

La pintura está aislada en ``_render_frame(painter, t)``; ``t=0`` (estático).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from desktop.theme import Theme, ThemeManager


def _css_color(token: str) -> QColor:
    """QColor no parsea la notación funcional CSS ``rgba(r, g, b, a)`` (PySide6 6.11:
    devuelve negro opaco). Soporta ``#RGB/#RRGGBB/#AARRGGBB`` y ``rgb()/rgba()``."""
    color = QColor(token)
    if color.isValid():
        return color
    body = token[token.find("(") + 1 : token.rfind(")")]
    parts = [part.strip() for part in body.split(",") if part.strip()]
    r, g, b = (int(part) for part in parts[:3])
    a = int(float(parts[3])) if len(parts) > 3 else 255
    color.setRgb(r, g, b, a)
    return color


class OrbitalBackground(QWidget):
    """Fondo: lienzo sólido con renglones tenues opcionales."""

    def __init__(
        self,
        theme: Theme | None = None,
        parent: QWidget | None = None,
        draw_rules: bool = True,
    ):
        super().__init__(parent)
        self.theme = theme or ThemeManager.current()
        self._draw_rules = draw_rules
        self._attached: QWidget | None = None

    def attach(self, container: QWidget) -> None:
        """Engancha el fondo a un contenedor: se re-dimensiona con él y no captura clicks."""
        if self._attached is container:
            return
        if self._attached is not None:
            self._attached.removeEventFilter(self)
        self._attached = container
        self.setParent(container)
        self.lower()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(container.rect())
        container.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        return super().eventFilter(obj, event)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._render_frame(painter, 0.0)
        finally:
            painter.end()

    def _render_frame(self, painter: QPainter, t: float) -> None:
        """Pinta un frame. ``t`` (tiempo normalizado 0-1) queda listo para animación."""
        c = self.theme.colors
        rect = self.rect()
        w = rect.width() or 1
        h = rect.height() or 1

        # Base: lienzo sólido neutro
        painter.fillRect(rect, QColor(c.bg_to))

        if self._draw_rules:
            # Renglones horizontales sutiles (papel nocturno)
            aa_was_on = painter.testRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            spacing = max(int(c.rule_spacing_px), 8)
            rule = _css_color(c.rule_color)
            pen = QPen(rule)
            pen.setCosmetic(True)
            pen.setWidthF(1.0)
            painter.setPen(pen)
            y = spacing
            while y < h:
                painter.drawLine(0, y, w, y)
                y += spacing
            if aa_was_on:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Anillos orbitales tenues
        for orbit in c.orbits:
            pen_color = QColor(orbit.color)
            pen_color.setAlphaF(orbit.alpha)
            pen = QPen(pen_color, 1.2)
            painter.setPen(pen)
            painter.save()
            painter.translate(orbit.x * w, orbit.y * h)
            painter.rotate(orbit.rotation)
            painter.drawEllipse(
                QRectF(
                    -orbit.rx * w,
                    -orbit.ry * h,
                    2 * orbit.rx * w,
                    2 * orbit.ry * h,
                )
            )
            painter.restore()
