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
    QKeyEvent,
)
from PySide6.QtWidgets import QWidget

from model.document import SIGNAL_COLORS, WaveformDocument
from model.markers import add_marker, sorted_markers
from ui.layout_metrics import DATA_TRACK_HEIGHT, RULER_HEIGHT, TRACK_HEIGHT, track_height_for


class WaveformView(QWidget):
    """Center pane that renders digital / bus waveforms for a document."""

    zoom_changed = Signal(float)
    cursor_changed = Signal(object)  # float | None — time in ns
    markers_changed = Signal()

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
        self._active_marker_ns: float | None = None
        self._drag_moved = False

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

    def add_marker_at_cursor(self) -> bool:
        """Drop a marker at the current cursor (or viewport left if unset)."""
        vs = self.document.view_state
        time_ns = vs.cursor_ns if vs.cursor_ns is not None else vs.pan_ns
        time_ns = max(0.0, time_ns)
        if not add_marker(vs.markers_ns, time_ns):
            return False
        self._active_marker_ns = time_ns
        self.markers_changed.emit()
        self.update()
        return True

    def clear_markers(self) -> None:
        vs = self.document.view_state
        if not vs.markers_ns:
            return
        vs.markers_ns.clear()
        self._active_marker_ns = None
        self.markers_changed.emit()
        self.update()

    def remove_marker(self, time_ns: float, *, snap_eps_ns: float = 0.001) -> bool:
        """Remove one marker near time_ns. Returns True if something was removed."""
        vs = self.document.view_state
        for index, existing in enumerate(vs.markers_ns):
            if abs(existing - time_ns) <= snap_eps_ns:
                del vs.markers_ns[index]
                if (
                    self._active_marker_ns is not None
                    and abs(self._active_marker_ns - time_ns) <= snap_eps_ns
                ):
                    self._active_marker_ns = None
                self.markers_changed.emit()
                self.update()
                return True
        return False

    def select_marker(self, time_ns: float) -> None:
        """Highlight a marker and move the cursor to it."""
        self._active_marker_ns = time_ns
        self.document.view_state.cursor_ns = time_ns
        self.cursor_changed.emit(time_ns)
        self.update()

    @property
    def active_marker_ns(self) -> float | None:
        return self._active_marker_ns

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
        self._draw_markers(painter, width, height)
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
            # Keep time labels in the lower band of the taller ruler.
            painter.drawText(x + 4, self._ruler_height - 14, f"{t_ns:.3f} ns")

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

    def _draw_markers(self, painter: QPainter, width: int, height: int) -> None:
        markers = sorted_markers(self.document.view_state.markers_ns)
        if not markers:
            return

        font = QFont("Menlo", 9)
        if not font.exactMatch():
            font = QFont("Courier New", 9)
        painter.setFont(font)

        for index, time_ns in enumerate(markers, start=1):
            x = int(round(self._time_to_x(time_ns)))
            if x < -2 or x > width + 2:
                continue

            is_active = (
                self._active_marker_ns is not None
                and abs(self._active_marker_ns - time_ns) < 1e-9
            )
            color = QColor("#fbbf24" if is_active else "#e5e7eb")
            pen = QPen(color, 2 if is_active else 1)
            painter.setPen(pen)
            painter.drawLine(x, self._ruler_height, x, height)

            # Marker tip near the bottom of the number band (above time labels).
            painter.setBrush(QBrush(color))
            tip_y = 22
            painter.drawPolygon(
                QPolygon(
                    [
                        QPoint(x, tip_y + 8),
                        QPoint(x - 5, tip_y),
                        QPoint(x + 5, tip_y),
                    ]
                )
            )
            painter.setPen(QColor("#111827" if is_active else "#f9fafb"))
            label = str(index)
            painter.drawText(x - 8, 1, 16, 14, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_cursor(self, painter: QPainter, width: int, height: int) -> None:
        cursor = self.document.view_state.cursor_ns
        if cursor is None:
            return
        x = int(round(self._time_to_x(cursor)))
        if 0 <= x <= width:
            painter.setPen(QPen(QColor("#38bdf8"), 1, Qt.PenStyle.DashLine))
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
            self._drag_moved = False
            self._drag_origin = event.position().toPoint()
            self._pan_origin_ns = self.document.view_state.pan_ns
            self.document.view_state.cursor_ns = max(0.0, self._x_to_time(event.position().x()))
            self.cursor_changed.emit(self.document.view_state.cursor_ns)
            self.update()
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            # Drop a marker at the clicked time.
            time_ns = max(0.0, self._x_to_time(event.position().x()))
            self.document.view_state.cursor_ns = time_ns
            self.cursor_changed.emit(time_ns)
            self.add_marker_at_cursor()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            dx = event.position().x() - self._drag_origin.x()
            if abs(dx) >= 3:
                self._drag_moved = True
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

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.clear_markers()
            event.accept()
            return
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
            event.accept()
            return
        if key == Qt.Key.Key_Minus:
            self.zoom_out()
            event.accept()
            return
        if key == Qt.Key.Key_F:
            self.fit_view()
            event.accept()
            return
        super().keyPressEvent(event)
