"""A small, self-contained iOS-style toggle switch.

Qt's stylesheets can reshape a QCheckBox's indicator box, but they cannot
draw a knob that visibly *slides* from one side to the other - that needs a
real paintEvent, not a border-radius trick. This widget is checkable and
exposes the same surface area callers already used on QCheckBox
(setChecked/isChecked/toggled/setToolTip), so it drops into
MainWindow.checkboxes without changing anything downstream - only how each
row is built changes (see app.py's toggle row helper).
"""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QSizePolicy

from . import theme

_TRACK_OFF = QColor(theme.BORDER_LIGHT)
_TRACK_ON = QColor(theme.ACCENT)
_KNOB = QColor("#ffffff")


def _lerp_colour(start: QColor, end: QColor, fraction: float) -> QColor:
    fraction = max(0.0, min(1.0, fraction))
    return QColor(
        round(start.red() + (end.red() - start.red()) * fraction),
        round(start.green() + (end.green() - start.green()) * fraction),
        round(start.blue() + (end.blue() - start.blue()) * fraction),
    )


class ToggleSwitch(QAbstractButton):
    """Checkable like QCheckBox (setChecked/isChecked/toggled all work the
    same way) - only the visual is different."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._position = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"knob_position", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate_to_state)

    def sizeHint(self) -> QSize:
        return QSize(44, 24)

    def _animate_to_state(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._position)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def get_knob_position(self) -> float:
        return self._position

    def set_knob_position(self, value: float) -> None:
        self._position = value
        self.update()

    knob_position = Property(float, get_knob_position, set_knob_position)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(0, 0, self.width(), self.height()).adjusted(1, 1, -1, -1)
        radius = rect.height() / 2

        if self.isEnabled():
            track = _lerp_colour(_TRACK_OFF, _TRACK_ON, self._position)
        else:
            track = QColor(theme.BG_SUNKEN)

        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, radius, radius)

        knob_diameter = rect.height() - 4
        travel = rect.width() - knob_diameter - 4
        knob_x = rect.left() + 2 + travel * self._position
        knob_rect = QRectF(knob_x, rect.top() + 2, knob_diameter, knob_diameter)
        painter.setBrush(_KNOB if self.isEnabled() else QColor(theme.TEXT_DIM))
        painter.drawEllipse(knob_rect)
