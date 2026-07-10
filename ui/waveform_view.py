"""Waveform canvas: time ruler, digital tracks, and DATA hex boxes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QImage,
    QPolygon,
    QBrush,
    QWheelEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import QWidget

from model.document import SIGNAL_COLORS, WaveformDocument
from ui.layout_metrics import DATA_TRACK_HEIGHT, RULER_HEIGHT, TRACK_HEIGHT, track_height_for


class WaveformView(QWidget):
    """Center pane that renders digital / bus waveforms for a document."""

    zoom_changed = Signal(float)
    cursor_changed = Signal(object)  # float | None — time in ns

    def __init__(self, document: WaveformDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self.setObjectName("waveformView")
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._ruler_height = RULER_HEIGHT
        self._track_height = TRACK_HEIGHT
        self._data_track_height = DATA_TRACK_HEIGHT
        self._pad_y = 4
        self._dragging = False
        self._drag_origin = QPoint()
        self._pan_origin_ns = 0.0

    # --- view controls ----------------------------------------------------

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
        timeline = self.document.timeline
        span = max(timeline.t_max_ns - timeline.t_min_ns, 1.0)
        width = max(self.width(), 1)
        # Fit full timeline into the viewport width.
        self.document.view_state.zoom_ps_per_px = (span * 1000.0) / width
        self.document.view_state.pan_ns = max(0.0, timeline.t_min_ns)
        self.zoom_changed.emit(self.document.view_state.zoom_ps_per_px)
        self.update()

    def _clamp_pan(self, pan_ns: float) -> float:
        """Keep the left edge of the viewport at or after 0 ns."""
        return max(0.0, pan_ns)

    def render_to_image(self) -> QImage:
        image = QImage(self.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#0a0a0a"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self._paint_contents(painter, self.width(), self.height())
        painter.end()
        return image

    # --- coordinate helpers -----------------------------------------------

    def _time_to_x(self, time_ns: float) -> float:
        vs = self.document.view_state
        return (time_ns - vs.pan_ns) * 1000.0 / vs.zoom_ps_per_px

    def _x_to_time(self, x: float) -> float:
        vs = self.document.view_state
        return vs.pan_ns + (x * vs.zoom_ps_per_px) / 1000.0

    def _visible_range(self, width: int) -> tuple[float, float]:
        t0 = self.document.view_state.pan_ns
        t1 = self._x_to_time(width)
        return t0, t1

    def _track_geometry(self) -> list[tuple[str, int, int]]:
        """Return (signal, y_top, height) for each track."""
        tracks: list[tuple[str, int, int]] = []
        y = self._ruler_height
        for name in self.document.signals:
            h = track_height_for(name)
            tracks.append((name, y, h))
            y += h
        return tracks

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self._paint_contents(painter, self.width(), self.height())
        painter.end()

    def _paint_contents(self, painter: QPainter, width: int, height: int) -> None:
        painter.fillRect(0, 0, width, height, QColor("#0a0a0a"))
        self._draw_grid(painter, width, height)
        self._draw_ruler(painter, width)
        self._draw_waveforms(painter, width, height)
        self._draw_cursor(painter, width, height)

    def _draw_grid(self, painter: QPainter, width: int, height: int) -> None:
        painter.setPen(QPen(QColor("#1f2937")))
        step_px = 80
        for x in range(0, width, step_px):
            painter.drawLine(x, self._ruler_height, x, height)

        for _name, y, h in self._track_geometry():
            painter.setPen(QPen(QColor("#1f2937")))
            painter.drawLine(0, y + h, width, y + h)

    def _draw_ruler(self, painter: QPainter, width: int) -> None:
        painter.fillRect(0, 0, width, self._ruler_height, QColor("#111827"))
        painter.setPen(QPen(QColor("#374151")))
        painter.drawLine(0, self._ruler_height - 1, width, self._ruler_height - 1)

        font = QFont("Menlo", 10)
        if not font.exactMatch():
            font = QFont("Courier New", 10)
        painter.setFont(font)

        step_px = 80
        for x in range(0, width, step_px):
            painter.setPen(QPen(QColor("#4b5563")))
            painter.drawLine(x, self._ruler_height - 8, x, self._ruler_height - 1)
            t_ns = self._x_to_time(x)
            painter.setPen(QColor("#d1d5db"))
            painter.drawText(x + 4, 18, f"{t_ns:.3f} ns")

    def _draw_waveforms(self, painter: QPainter, width: int, height: int) -> None:
        timeline = self.document.timeline
        has_edges = bool(timeline.edges) or bool(timeline.bus_segments)
        if not has_edges:
            self._draw_empty_message(painter)
            return

        for name, y, h in self._track_geometry():
            if y > height:
                break
            color = QColor(SIGNAL_COLORS.get(name, "#e2e8f0"))
            if name == "DATA":
                self._draw_bus_track(painter, y, h, width, color)
            else:
                self._draw_digital_track(painter, name, y, h, width, color)

    def _draw_digital_track(
        self,
        painter: QPainter,
        signal: str,
        y: int,
        h: int,
        width: int,
        color: QColor,
    ) -> None:
        edges = self.document.timeline.edges_for(signal)
        if not edges:
            return

        y_high = y + self._pad_y
        y_low = y + h - self._pad_y
        t0, t1 = self._visible_range(width)

        # Walk edges and build a step polyline across the visible window.
        level = edges[0].value
        # Find level at t0
        for edge in edges:
            if edge.time_ns <= t0:
                level = edge.value
            else:
                break

        points: list[QPoint] = []
        x = 0
        y_level = y_high if level else y_low
        points.append(QPoint(x, y_level))

        for edge in edges:
            if edge.time_ns < t0:
                continue
            if edge.time_ns > t1:
                break
            ex = int(round(self._time_to_x(edge.time_ns)))
            # horizontal to transition, then vertical
            points.append(QPoint(ex, y_level))
            level = edge.value
            y_level = y_high if level else y_low
            points.append(QPoint(ex, y_level))

        points.append(QPoint(width, y_level))

        pen = QPen(color, 2)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        for i in range(len(points) - 1):
            painter.drawLine(points[i], points[i + 1])

    def _draw_bus_track(
        self,
        painter: QPainter,
        y: int,
        h: int,
        width: int,
        color: QColor,
    ) -> None:
        t0, t1 = self._visible_range(width)
        font = QFont("Menlo", 10)
        if not font.exactMatch():
            font = QFont("Courier New", 10)
        painter.setFont(font)

        for seg in self.document.timeline.bus_segments:
            seg_end = seg.time_ns + seg.duration_ns
            if seg_end < t0 or seg.time_ns > t1:
                continue

            x0 = max(0, int(round(self._time_to_x(seg.time_ns))))
            x1 = min(width, int(round(self._time_to_x(seg_end))))
            if x1 - x0 < 4:
                continue

            top = y + 4
            bottom = y + h - 4
            mid = (top + bottom) // 2
            notch = min(8, (x1 - x0) // 4)

            # Hexagon / angled bus box
            poly = QPolygon(
                [
                    QPoint(x0, mid),
                    QPoint(x0 + notch, top),
                    QPoint(x1 - notch, top),
                    QPoint(x1, mid),
                    QPoint(x1 - notch, bottom),
                    QPoint(x0 + notch, bottom),
                ]
            )
            fill = QColor(color)
            fill.setAlpha(45)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(color, 1))
            painter.drawPolygon(poly)

            painter.setPen(QColor("#e2e8f0"))
            text_rect_width = max(0, x1 - x0 - 2 * notch)
            if text_rect_width >= 16:
                painter.drawText(
                    x0 + notch,
                    top,
                    text_rect_width,
                    bottom - top,
                    Qt.AlignmentFlag.AlignCenter,
                    seg.value_hex,
                )

    def _draw_cursor(self, painter: QPainter, width: int, height: int) -> None:
        cursor = self.document.view_state.cursor_ns
        if cursor is None:
            return
        x = int(round(self._time_to_x(cursor)))
        if 0 <= x <= width:
            painter.setPen(QPen(QColor("#f8fafc"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(x, self._ruler_height, x, height)

    def _draw_empty_message(self, painter: QPainter) -> None:
        painter.setPen(QColor("#6b7280"))
        painter.setFont(QFont("Helvetica Neue", 12))
        painter.drawText(
            self.rect().adjusted(0, self._ruler_height, 0, 0),
            Qt.AlignmentFlag.AlignCenter,
            f"{self.document.title}\n\nNo waveform data yet.",
        )

    # --- interaction ------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.angleDelta().y() > 0:
            self.zoom_in()
        elif event.angleDelta().y() < 0:
            self.zoom_out()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_origin = event.position().toPoint()
            self._pan_origin_ns = self.document.view_state.pan_ns
            self.document.view_state.cursor_ns = self._x_to_time(event.position().x())
            self.cursor_changed.emit(self.document.view_state.cursor_ns)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            dx = event.position().x() - self._drag_origin.x()
            delta_ns = -(dx * self.document.view_state.zoom_ps_per_px) / 1000.0
            self.document.view_state.pan_ns = self._clamp_pan(self._pan_origin_ns + delta_ns)
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)
