"""Right panel: marker list / search placeholders."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from model.document import WaveformDocument
from model.markers import marker_rows


class EventPanel(QWidget):
    """List View and Search View tabs bound to the active document."""

    marker_selected = Signal(float)  # time_ns
    marker_delete_requested = Signal(float)  # time_ns

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("eventPanel")
        self.setMinimumWidth(260)
        self._document: WaveformDocument | None = None
        self._updating = False

        self.tabs = QTabWidget()
        self.list_table = self._make_event_table()
        self.search_table = self._make_event_table()
        self.list_table.itemSelectionChanged.connect(self._on_list_selection)

        self.delete_marker_button = QPushButton("Delete Marker")
        self.delete_marker_button.setEnabled(False)
        self.delete_marker_button.setToolTip(
            "Delete the selected marker (Backspace also works when the list is focused)"
        )
        self.delete_marker_button.clicked.connect(self._on_delete_clicked)

        self._delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self.list_table)
        self._delete_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._delete_shortcut.activated.connect(self._on_delete_clicked)

        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(6)
        list_layout.addWidget(self.list_table, 1)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(8, 0, 8, 8)
        button_row.addStretch(1)
        button_row.addWidget(self.delete_marker_button)
        list_layout.addLayout(button_row)

        search_page = QWidget()
        search_layout = QVBoxLayout(search_page)
        search_layout.setContentsMargins(8, 8, 8, 8)
        search_layout.setSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search events (coming later)…")
        self.search_input.setEnabled(False)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.search_table)

        self.tabs.addTab(list_page, "List View")
        self.tabs.addTab(search_page, "Search View")

        title = QLabel("Events")
        title.setObjectName("panelTitle")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(self.tabs)

        self._show_placeholder(self.list_table, "No markers yet — right-click the waveform.")
        self._show_placeholder(self.search_table, "Search will filter decoded events.")

    def _make_event_table(self) -> QTableWidget:
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Mark", "Sample", "Time(ns)", "Diff"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    def _show_placeholder(self, table: QTableWidget, message: str) -> None:
        table.clearSpans()
        table.setRowCount(1)
        item = QTableWidgetItem(message)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(0, 0, item)
        table.setSpan(0, 0, 1, 4)
        if table is self.list_table:
            self.delete_marker_button.setEnabled(False)

    def bind_document(self, document: WaveformDocument | None) -> None:
        self._document = document
        self.refresh_markers()

    def refresh_markers(self) -> None:
        self.list_table.clearSpans()
        self.search_table.clearSpans()
        self.list_table.setRowCount(0)
        self.search_table.setRowCount(0)
        self.delete_marker_button.setEnabled(False)

        if self._document is None:
            self._show_placeholder(self.list_table, "Open a log to view markers.")
            self._show_placeholder(self.search_table, "Open a log to search events.")
            return

        rows = marker_rows(self._document.view_state.markers_ns)
        if not rows:
            self._show_placeholder(
                self.list_table,
                "No markers yet — right-click the waveform to add one.",
            )
            self._show_placeholder(
                self.search_table,
                "Search will filter decoded events.",
            )
            return

        self._updating = True
        try:
            self.list_table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                mark_item = QTableWidgetItem(str(row["mark"]))
                mark_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                mark_item.setData(Qt.ItemDataRole.UserRole, float(row["time_ns"]))

                sample_item = QTableWidgetItem(str(row["sample"]))
                sample_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                time_item = QTableWidgetItem(f"{float(row['time_ns']):.3f}")
                time_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                diff = row["diff"]
                if isinstance(diff, str):
                    diff_text = diff
                else:
                    diff_text = f"{float(diff):.3f}"
                diff_item = QTableWidgetItem(diff_text)
                diff_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                self.list_table.setItem(row_index, 0, mark_item)
                self.list_table.setItem(row_index, 1, sample_item)
                self.list_table.setItem(row_index, 2, time_item)
                self.list_table.setItem(row_index, 3, diff_item)
        finally:
            self._updating = False

        self._show_placeholder(
            self.search_table,
            "Search will filter decoded events.",
        )
        self._update_delete_button()

    def select_marker_time(self, time_ns: float) -> None:
        """Select the list row that matches the given marker time."""
        self._updating = True
        try:
            self.list_table.clearSelection()
            for row in range(self.list_table.rowCount()):
                item = self.list_table.item(row, 0)
                if item is None:
                    continue
                stored = item.data(Qt.ItemDataRole.UserRole)
                if stored is not None and abs(float(stored) - time_ns) < 1e-9:
                    self.list_table.selectRow(row)
                    self.list_table.scrollToItem(item)
                    break
        finally:
            self._updating = False
        self._update_delete_button()

    def _selected_marker_time(self) -> float | None:
        items = self.list_table.selectedItems()
        if not items:
            return None
        item = self.list_table.item(items[0].row(), 0)
        if item is None:
            return None
        stored = item.data(Qt.ItemDataRole.UserRole)
        if stored is None:
            return None
        return float(stored)

    def _update_delete_button(self) -> None:
        self.delete_marker_button.setEnabled(self._selected_marker_time() is not None)

    def _on_list_selection(self) -> None:
        self._update_delete_button()
        if self._updating:
            return
        time_ns = self._selected_marker_time()
        if time_ns is None:
            return
        self.marker_selected.emit(time_ns)

    def _on_delete_clicked(self) -> None:
        time_ns = self._selected_marker_time()
        if time_ns is None:
            return
        self.marker_delete_requested.emit(time_ns)
