"""A card with one diagonal-cut corner - Battlefield's own deploy-screen
panel language (every panel in the game's loadout/deploy UI shares this same
single-corner bevel). Qt's stylesheet engine has no clip-path, so this
paints its own background/border/left-accent-stripe rather than leaning on
QFrame's box model - same reasoning as toggle_switch.py's custom paintEvent.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame

from . import theme

CUT = 16  # px - size of the single diagonal corner cut (bottom-right)


class CutCornerCard(QFrame):
    """Drop-in replacement for the old plain QFrame#Card: same margins/
    layout behaviour, a differently painted shape. Child widgets are laid
    out exactly as before (see app.py's card()) - only paintEvent changes."""

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        cut = max(0.0, min(CUT, rect.height() / 3, rect.width() / 3))

        path = QPainterPath()
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom() - cut)
        path.lineTo(rect.right() - cut, rect.bottom())
        path.lineTo(rect.left(), rect.bottom())
        path.closeSubpath()

        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0, QColor(theme.BG_RAISED_TOP))
        gradient.setColorAt(1, QColor(theme.BG_RAISED))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawPath(path)

        pen = QPen(QColor(theme.BORDER_LIGHT))
        pen.setWidthF(1)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        # The left accent stripe the old QSS rule drew (border-left: 3px
        # solid ACCENT) - the left edge is untouched by the cut, so a plain
        # rect is safe here.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRect(QRectF(rect.left(), rect.top(), 3.0, rect.height()))
