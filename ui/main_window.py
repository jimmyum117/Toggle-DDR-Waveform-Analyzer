"""Main application window: menus, toolbar, splitters, and tabbed waveforms."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
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
from ui.signal_list import SignalListWidget
from ui.waveform_page import WaveformPage


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Toggle DDR Waveform Analyzer")
        self.resize(1400, 820)

        self.signal_list = SignalListWidget()
        self.event_panel = EventPanel()
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

        self._zoom_label = QLabel("217.1 ps/px")
        self._file_label = QLabel("No file open")
        status = QStatusBar()
        status.addWidget(self._file_label, 1)
        status.addPermanentWidget(self._zoom_label)
        self.setStatusBar(status)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._update_actions()
        self.signal_list.bind_document(None)
        self.event_panel.bind_document(None)

    # --- actions / chrome -------------------------------------------------

    def _build_actions(self) -> None:
        self.open_action = QAction("Open…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.setStatusTip("Open a log file in a new tab")
        self.open_action.triggered.connect(self.open_log)

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

        self.toggle_list_action = QAction("Toggle Event Panel", self)
        self.toggle_list_action.setCheckable(True)
        self.toggle_list_action.setChecked(True)
        self.toggle_list_action.triggered.connect(self._toggle_event_panel)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.open_action)
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
        view_menu.addAction(self.toggle_list_action)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        for action in (
            self.open_action,
            self.close_tab_action,
            self.save_image_action,
        ):
            toolbar.addAction(action)
        toolbar.addSeparator()
        for action in (self.zoom_in_action, self.zoom_out_action, self.fit_action):
            toolbar.addAction(action)

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
        page = WaveformPage(document)
        page.waveform_view.zoom_changed.connect(self._on_zoom_changed)
        index = self.tabs.addTab(page, document.title)
        self.tabs.setTabToolTip(index, str(path))
        self.tabs.setCurrentIndex(index)
        self.statusBar().showMessage(f"Opened {path.name} (parsing not implemented yet)", 4000)

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
            self._zoom_label.setText("—")

    def _on_tab_changed(self, index: int) -> None:
        page = self._current_page()
        document = page.document if page else None
        self.signal_list.bind_document(document)
        self.event_panel.bind_document(document)
        self._update_actions()
        if document is None:
            self._file_label.setText("No file open")
            self._zoom_label.setText("—")
            return
        path_text = str(document.path) if document.path else document.title
        self._file_label.setText(path_text)
        self._zoom_label.setText(f"{document.view_state.zoom_ps_per_px:.1f} ps/px")

    def _update_actions(self) -> None:
        has_tab = self.tabs.count() > 0
        self.close_tab_action.setEnabled(has_tab)
        self.close_all_action.setEnabled(has_tab)
        self.save_image_action.setEnabled(has_tab)
        self.zoom_in_action.setEnabled(has_tab)
        self.zoom_out_action.setEnabled(has_tab)
        self.fit_action.setEnabled(has_tab)

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

    def _on_zoom_changed(self, zoom_ps_per_px: float) -> None:
        self._zoom_label.setText(f"{zoom_ps_per_px:.1f} ps/px")

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
