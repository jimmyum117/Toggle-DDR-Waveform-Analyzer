"""Right panel: event list / search placeholders (filled when parser exists)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QLineEdit,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from model.document import WaveformDocument


class EventPanel(QWidget):
    """List View and Search View tabs bound to the active document."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("eventPanel")
        self.setMinimumWidth(260)

        self.tabs = QTabWidget()
        self.list_table = self._make_event_table()
        self.search_table = self._make_event_table()

        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.addWidget(self.list_table)

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

        self._show_placeholder(self.list_table, "No events — log parser not implemented yet.")
        self._show_placeholder(self.search_table, "Search will filter decoded events.")

    def _make_event_table(self) -> QTableWidget:
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Mark", "Sample", "Time(ns)", "Diff"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    def _show_placeholder(self, table: QTableWidget, message: str) -> None:
        table.setRowCount(1)
        item = QTableWidgetItem(message)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(0, 0, item)
        table.setSpan(0, 0, 1, 4)

    def bind_document(self, document: WaveformDocument | None) -> None:
        self.list_table.clearSpans()
        self.search_table.clearSpans()
        self.list_table.setRowCount(0)
        self.search_table.setRowCount(0)

        if document is None:
            self._show_placeholder(self.list_table, "Open a log to view events.")
            self._show_placeholder(self.search_table, "Open a log to search events.")
            return

        # Placeholder rows so the table layout is visible before parsing exists.
        self._show_placeholder(
            self.list_table,
            f"Events for “{document.title}” will appear here after parsing.",
        )
        self._show_placeholder(
            self.search_table,
            "Search will filter decoded events.",
        )
