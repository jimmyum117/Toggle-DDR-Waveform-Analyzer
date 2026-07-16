"""Main application window: menus, toolbar, splitters, and tabbed waveforms."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QTimer, QEvent
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from model.document import WaveformDocument
from ui.event_panel import EventPanel
from ui.layout_metrics import RULER_HEIGHT
from ui.signal_list import SignalListWidget
from ui.waveform_page import WaveformPage


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Toggle DDR Waveform Analyzer")
        self.resize(1400, 820)
        self._idle_tab_counter = 0
        self._aligning_signals = False
        self._align_followup_pending = False
        self._align_timer: QTimer | None = None

        self.signal_list = SignalListWidget()
        self.event_panel = EventPanel()
        self.event_panel.marker_selected.connect(self._on_marker_selected)
        self.event_panel.marker_delete_requested.connect(self._on_marker_delete_requested)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab_at)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.tabs)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.signal_list)
        splitter.addWidget(center)
        splitter.addWidget(self.event_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([200, 900, 300])

        container = QWidget()
        root = QHBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)
        self.setCentralWidget(container)

        self._zoom_label = QLabel("Zoom: —")
        self._cursor_label = QLabel("Cursor: —")
        self._file_label = QLabel("No file open")
        status = QStatusBar()
        status.addWidget(self._file_label, 1)
        status.addPermanentWidget(self._cursor_label)
        status.addPermanentWidget(self._zoom_label)
        self.setStatusBar(status)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._update_actions()
        self.signal_list.bind_document(None)
        self.event_panel.bind_document(None)
        self._schedule_signal_list_alignment()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_signal_list_alignment(follow_up=True)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_signal_list_alignment()

    def eventFilter(self, watched, event):  # noqa: N802
        # Waveform geometry often settles after the tab is first shown; re-align then.
        if event.type() in (
            QEvent.Type.Show,
            QEvent.Type.Resize,
            QEvent.Type.Move,
        ):
            page = self._current_page()
            if page is not None and watched is page.waveform_view:
                follow_up = event.type() == QEvent.Type.Show
                self._schedule_signal_list_alignment(follow_up=follow_up)
        return super().eventFilter(watched, event)

    def _schedule_signal_list_alignment(self, follow_up: bool = False) -> None:
        """Coalesce alignment requests until the next event-loop turn."""
        if follow_up:
            self._align_followup_pending = True
        if self._align_timer is None:
            self._align_timer = QTimer(self)
            self._align_timer.setSingleShot(True)
            self._align_timer.timeout.connect(self._sync_signal_list_alignment)
        self._align_timer.start(0)

    def _sync_signal_list_alignment(self) -> None:
        """Align pin rows to waveform tracks using live widget geometry."""
        if self._aligning_signals or not self.isVisible():
            return

        page = self._current_page()
        if page is None:
            self.signal_list.set_top_spacer_height(self.tabs.tabBar().height())
            return

        waveform = page.waveform_view
        table = self.signal_list.table
        if table.rowCount() == 0:
            return

        # Skip until the waveform has a real on-screen size (initial open race).
        if waveform.width() <= 1 or waveform.height() <= 1 or not waveform.isVisible():
            self._schedule_signal_list_alignment(follow_up=True)
            return

        self._aligning_signals = True
        try:
            target_y = waveform.mapToGlobal(QPoint(0, RULER_HEIGHT)).y()
            current_y = table.viewport().mapToGlobal(QPoint(0, 0)).y()
            # Absolute spacer from current geometry (avoids stacking stale deltas).
            new_height = max(0, self.signal_list.top_spacer_height() + (target_y - current_y))
            if new_height != self.signal_list.top_spacer_height():
                self.signal_list.set_top_spacer_height(new_height)
                layout = self.signal_list.layout()
                if layout is not None:
                    layout.activate()
                self.signal_list.updateGeometry()
        finally:
            self._aligning_signals = False

        if self._align_followup_pending:
            self._align_followup_pending = False
            # One more pass after the spacer change has been realized on screen.
            QTimer.singleShot(0, self._sync_signal_list_alignment)

    # --- actions / chrome -------------------------------------------------

    def _build_actions(self) -> None:
        self.open_action = QAction("Open…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.setStatusTip("Open a log file in a new tab")
        self.open_action.triggered.connect(self.open_log)

        self.new_tab_action = QAction("New Tab (Idle)", self)
        self.new_tab_action.setShortcut(QKeySequence.StandardKey.New)
        self.new_tab_action.setStatusTip(
            "Temporary: open a demo tab with all pins at inactive levels"
        )
        self.new_tab_action.triggered.connect(self.new_idle_tab)

        self.close_tab_action = QAction("Close Tab", self)
        self.close_tab_action.setShortcut(QKeySequence.StandardKey.Close)
        self.close_tab_action.setStatusTip("Close the active waveform tab")
        self.close_tab_action.triggered.connect(self.close_current_tab)

        self.close_all_action = QAction("Close All", self)
        self.close_all_action.setStatusTip("Close all waveform tabs")
        self.close_all_action.triggered.connect(self.close_all_tabs)

        self.save_image_action = QAction("Save Image…", self)
        self.save_image_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_image_action.setStatusTip("Save the active waveform viewport as a PNG")
        self.save_image_action.triggered.connect(self.save_image)

        self.quit_action = QAction("Quit", self)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.quit_action.triggered.connect(self.close)

        self.zoom_in_action = QAction("Zoom In", self)
        self.zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        self.zoom_in_action.triggered.connect(self.zoom_in)

        self.zoom_out_action = QAction("Zoom Out", self)
        self.zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        self.zoom_out_action.triggered.connect(self.zoom_out)

        self.fit_action = QAction("Fit", self)
        self.fit_action.setShortcut(QKeySequence("Ctrl+0"))
        self.fit_action.setStatusTip("Reset zoom for the active tab")
        self.fit_action.triggered.connect(self.fit_view)

        self.clear_markers_action = QAction("Clear Markers", self)
        self.clear_markers_action.setShortcut(QKeySequence("Escape"))
        self.clear_markers_action.setStatusTip("Remove all markers on the active tab")
        self.clear_markers_action.triggered.connect(self.clear_markers)

        self.toggle_list_action = QAction("Toggle Event Panel", self)
        self.toggle_list_action.setCheckable(True)
        self.toggle_list_action.setChecked(True)
        self.toggle_list_action.triggered.connect(self._toggle_event_panel)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.open_action)
        file_menu.addAction(self.new_tab_action)
        file_menu.addAction(self.close_tab_action)
        file_menu.addAction(self.close_all_action)
        file_menu.addSeparator()
        file_menu.addAction(self.save_image_action)
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.zoom_in_action)
        view_menu.addAction(self.zoom_out_action)
        view_menu.addAction(self.fit_action)
        view_menu.addSeparator()
        view_menu.addAction(self.clear_markers_action)
        view_menu.addSeparator()
        view_menu.addAction(self.toggle_list_action)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        for action in (
            self.open_action,
            self.new_tab_action,
            self.save_image_action,
        ):
            toolbar.addAction(action)
        toolbar.addSeparator()
        for action in (self.zoom_in_action, self.zoom_out_action, self.fit_action):
            toolbar.addAction(action)
        toolbar.addSeparator()
        toolbar.addAction(self.clear_markers_action)

    # --- document / tab management ----------------------------------------

    def _current_page(self) -> WaveformPage | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, WaveformPage) else None

    def open_log(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open Log File",
            "",
            "Log files (*.log *.txt *.csv);;All files (*)",
        )
        if not path_str:
            return
        self.open_path(Path(path_str))

    def open_path(self, path: Path) -> None:
        document = WaveformDocument.from_path(path)
        self._add_document_tab(document, tooltip=str(path))
        self.statusBar().showMessage(f"Opened {path.name} (parsing not implemented yet)", 4000)

    def new_idle_tab(self) -> None:
        """Temporary helper: demo tab with every pin held inactive."""
        self._idle_tab_counter += 1
        document = WaveformDocument.idle_demo(self._idle_tab_counter)
        self._add_document_tab(document, tooltip=document.note)
        self.statusBar().showMessage(
            "Opened idle demo — pins at inactive levels (CE/WE/RE/RB high, CLE/ALE low)",
            5000,
        )

    def _add_document_tab(self, document: WaveformDocument, tooltip: str = "") -> None:
        page = WaveformPage(document)
        page.waveform_view.zoom_changed.connect(self._on_zoom_changed)
        page.waveform_view.cursor_changed.connect(self._on_cursor_changed)
        page.waveform_view.markers_changed.connect(self._on_markers_changed)
        page.waveform_view.installEventFilter(self)
        index = self.tabs.addTab(page, document.title)
        if tooltip:
            self.tabs.setTabToolTip(index, tooltip)
        self.tabs.setCurrentIndex(index)
        # Fit idle/demo timelines so the full span is visible.
        if document.timeline.edges:
            page.waveform_view.fit_view()
        self._update_actions()
        self._refresh_status_for_document(document)
        self._schedule_signal_list_alignment(follow_up=True)
        # Tab layout can settle after the first deferred sync on some platforms.
        QTimer.singleShot(50, self._sync_signal_list_alignment)

    def close_current_tab(self) -> None:
        index = self.tabs.currentIndex()
        if index >= 0:
            self._close_tab_at(index)

    def close_all_tabs(self) -> None:
        while self.tabs.count():
            self._close_tab_at(0)

    def _close_tab_at(self, index: int) -> None:
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()
        self._update_actions()
        if self.tabs.count() == 0:
            self.signal_list.bind_document(None)
            self.event_panel.bind_document(None)
            self._file_label.setText("No file open")
            self._zoom_label.setText("Zoom: —")
            self._cursor_label.setText("Cursor: —")

    def _on_tab_changed(self, index: int) -> None:
        page = self._current_page()
        document = page.document if page else None
        self.signal_list.bind_document(document)
        self.event_panel.bind_document(document)
        self._update_actions()
        self._refresh_status_for_document(document)
        self._schedule_signal_list_alignment(follow_up=True)

    def _refresh_status_for_document(self, document: WaveformDocument | None) -> None:
        if document is None:
            self._file_label.setText("No file open")
            self._zoom_label.setText("—")
            self._set_cursor_label(None)
            return
        path_text = str(document.path) if document.path else document.title
        self._file_label.setText(path_text)
        self._zoom_label.setText(f"Zoom: {document.view_state.zoom_ps_per_px:.1f} ps/px")
        self._set_cursor_label(document.view_state.cursor_ns)

    def _set_cursor_label(self, cursor_ns: float | None) -> None:
        if cursor_ns is None:
            self._cursor_label.setText("Cursor: —")
        else:
            self._cursor_label.setText(f"Cursor: {cursor_ns:.3f} ns")

    def _update_actions(self) -> None:
        has_tab = self.tabs.count() > 0
        self.close_tab_action.setEnabled(has_tab)
        self.close_all_action.setEnabled(has_tab)
        self.save_image_action.setEnabled(has_tab)
        self.zoom_in_action.setEnabled(has_tab)
        self.zoom_out_action.setEnabled(has_tab)
        self.fit_action.setEnabled(has_tab)
        self.clear_markers_action.setEnabled(has_tab)

    # --- view / export ----------------------------------------------------

    def zoom_in(self) -> None:
        page = self._current_page()
        if page:
            page.waveform_view.zoom_in()

    def zoom_out(self) -> None:
        page = self._current_page()
        if page:
            page.waveform_view.zoom_out()

    def fit_view(self) -> None:
        page = self._current_page()
        if page:
            page.waveform_view.fit_view()

    def clear_markers(self) -> None:
        page = self._current_page()
        if page is None:
            return
        page.waveform_view.clear_markers()
        self.statusBar().showMessage("Markers cleared", 2000)

    def _on_zoom_changed(self, zoom_ps_per_px: float) -> None:
        self._zoom_label.setText(f"{zoom_ps_per_px:.1f} ps/px")

    def _on_cursor_changed(self, cursor_ns: object) -> None:
        if self.sender() is not None:
            page = self._current_page()
            if page is None or page.waveform_view is not self.sender():
                return
        self._set_cursor_label(cursor_ns if isinstance(cursor_ns, (int, float)) else None)
        self.signal_list.refresh_values()

    def _on_markers_changed(self) -> None:
        page = self._current_page()
        if page is None or page.waveform_view is not self.sender():
            return
        self.event_panel.refresh_markers()
        active = page.waveform_view.active_marker_ns
        if active is not None:
            self.event_panel.select_marker_time(active)

    def _on_marker_selected(self, time_ns: float) -> None:
        page = self._current_page()
        if page is None:
            return
        page.waveform_view.select_marker(time_ns)
        self._set_cursor_label(time_ns)

    def _on_marker_delete_requested(self, time_ns: float) -> None:
        page = self._current_page()
        if page is None:
            return
        if page.waveform_view.remove_marker(time_ns):
            self.statusBar().showMessage("Marker deleted", 2000)

    def _toggle_event_panel(self, checked: bool) -> None:
        self.event_panel.setVisible(checked)

    def save_image(self) -> None:
        page = self._current_page()
        if page is None:
            return

        default_name = f"{page.document.title}_waveform.png"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save Waveform Image",
            default_name,
            "PNG Image (*.png)",
        )
        if not path_str:
            return

        if not path_str.lower().endswith(".png"):
            path_str += ".png"

        image = page.waveform_view.render_to_image()
        if not image.save(path_str, "PNG"):
            QMessageBox.warning(self, "Save Failed", f"Could not write:\n{path_str}")
            return

        self.statusBar().showMessage(f"Saved {path_str}", 5000)
