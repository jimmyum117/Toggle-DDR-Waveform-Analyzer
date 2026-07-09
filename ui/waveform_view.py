"""Waveform canvas placeholder.

Real Toggle DDR edge drawing and timing will be implemented later.
This widget provides the dark viewport, time ruler chrome, and image export.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QImage
from PySide6.QtWidgets import QWidget

from model.document import SIGNAL_COLORS, WaveformDocument


class WaveformView(QWidget):
    """Center pane that will eventually render digital / bus waveforms."""

    zoom_changed = Signal(float)

    def __init__(self, document: WaveformDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self.setObjectName("waveformView")
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._ruler_height = 36
        self._track_height = 28

    def zoom_in(self) -> None:
        vs = self.document.view_state
        vs.zoom_ps_per_px = max(1.0, vs.zoom_ps_per_px / 1.5)
        self.zoom_changed.emit(vs.zoom_ps_per_px)
        self.update()

    def zoom_out(self) -> None:
        vs = self.document.view_state
        vs.zoom_ps_per_px = min(1_000_000.0, vs.zoom_ps_per_px * 1.5)
        self.zoom_changed.emit(vs.zoom_ps_per_px)
        self.update()

    def fit_view(self) -> None:
        # Placeholder: reset to a default scale until real timing data exists.
        self.document.view_state.zoom_ps_per_px = 217.1
        self.document.view_state.pan_ns = 0.0
        self.zoom_changed.emit(self.document.view_state.zoom_ps_per_px)
        self.update()

    def render_to_image(self) -> QImage:
        """Rasterize the current viewport for Save Image."""
        image = QImage(self.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#0a0a0a"))
        painter = QPainter(image)
        self._paint_contents(painter, self.width(), self.height())
        painter.end()
        return image

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        self._paint_contents(painter, self.width(), self.height())
        painter.end()

    def _paint_contents(self, painter: QPainter, width: int, height: int) -> None:
        painter.fillRect(0, 0, width, height, QColor("#0a0a0a"))
        self._draw_ruler(painter, width)
        self._draw_tracks(painter, width, height)
        self._draw_placeholder_message(painter, width, height)

    def _draw_ruler(self, painter: QPainter, width: int) -> None:
        painter.fillRect(0, 0, width, self._ruler_height, QColor("#111827"))
        painter.setPen(QPen(QColor("#374151")))
        painter.drawLine(0, self._ruler_height - 1, width, self._ruler_height - 1)

        painter.setPen(QColor("#9ca3af"))
        font = QFont("Menlo", 10)
        if not font.exactMatch():
            font = QFont("Courier New", 10)
        painter.setFont(font)

        vs = self.document.view_state
        # Decorative tick marks only — real time mapping comes later.
        step_px = 80
        for x in range(0, width, step_px):
            painter.setPen(QPen(QColor("#4b5563")))
            painter.drawLine(x, self._ruler_height - 8, x, self._ruler_height - 1)
            t_ns = vs.pan_ns + (x * vs.zoom_ps_per_px) / 1000.0
            painter.setPen(QColor("#d1d5db"))
            painter.drawText(x + 4, 18, f"{t_ns:.3f} ns")

    def _draw_tracks(self, painter: QPainter, width: int, height: int) -> None:
        y = self._ruler_height
        for name in self.document.signals:
            track_bottom = y + self._track_height
            if y > height:
                break

            # Subtle track separator
            painter.setPen(QPen(QColor("#1f2937")))
            painter.drawLine(0, track_bottom, width, track_bottom)

            # Placeholder mid-line for digital tracks (DATA gets a thicker band)
            color = QColor(SIGNAL_COLORS.get(name, "#e2e8f0"))
            color.setAlpha(40)
            mid_y = y + self._track_height // 2
            if name == "DATA":
                painter.fillRect(0, y + 4, width, self._track_height - 8, color)
            else:
                painter.setPen(QPen(color, 1, Qt.PenStyle.DotLine))
                painter.drawLine(0, mid_y, width, mid_y)

            y = track_bottom

        # Vertical guide lines (placeholder grid)
        painter.setPen(QPen(QColor("#1f2937")))
        for x in range(0, width, 80):
            painter.drawLine(x, self._ruler_height, x, height)

    def _draw_placeholder_message(self, painter: QPainter, width: int, height: int) -> None:
        painter.setPen(QColor("#6b7280"))
        font = QFont("Segoe UI", 12)
        painter.setFont(font)
        message = (
            f"{self.document.title}\n\n"
            "Waveform drawing not implemented yet.\n"
            "Open a log to create a tab; parsing and pin timing come later."
        )
        painter.drawText(
            self.rect().adjusted(0, self._ruler_height, 0, 0),
            Qt.AlignmentFlag.AlignCenter,
            message,
        )
