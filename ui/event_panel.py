"""Right panel: marker list and waveform search."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from model.document import DIGITAL_SIGNALS, WaveformDocument
from model.markers import marker_rows
from model.search import (
    parse_data_byte,
    search_data_value,
    search_edge,
    search_hit_rows,
    search_signal_states,
)


class EventPanel(QWidget):
    """List View and Search View tabs bound to the active document."""

    marker_selected = Signal(float)  # time_ns
    marker_delete_requested = Signal(float)  # time_ns
    search_hit_selected = Signal(float)  # time_ns

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("eventPanel")
        self.setMinimumWidth(260)
        self._document: WaveformDocument | None = None
        self._updating = False
        self._state_rows: list[tuple[QComboBox, QComboBox, QPushButton, QWidget]] = []

        self.tabs = QTabWidget()
        self.list_table = self._make_table(["Mark", "Sample", "Time(ns)", "Diff"])
        self.search_table = self._make_table(["#", "Match", "Time(ns)", "Diff"])
        self.list_table.itemSelectionChanged.connect(self._on_list_selection)
        self.search_table.itemSelectionChanged.connect(self._on_search_selection)

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

        search_page = self._build_search_page()

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
        self._show_placeholder(
            self.search_table,
            "Search DATA, edges, or multi-signal states.",
        )
        self._add_state_row("CLE", 1)
        self._add_state_row("ALE", 0)
        self._add_state_row("CE0", 0)
        self._on_search_mode_changed(self.search_mode.currentIndex())

    def _build_search_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_label = QLabel("Find")
        self.search_mode = QComboBox()
        self.search_mode.addItem("Data value", "data")
        self.search_mode.addItem("Rising edge", "rising")
        self.search_mode.addItem("Falling edge", "falling")
        self.search_mode.addItem("Signal states", "states")
        self.search_mode.currentIndexChanged.connect(self._on_search_mode_changed)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.search_mode, 1)
        layout.addLayout(mode_row)

        self.search_stack = QStackedWidget()

        data_page = QWidget()
        data_layout = QHBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(6)
        self.data_input = QLineEdit()
        self.data_input.setPlaceholderText("e.g. 0xFF, FF, or 255")
        self.data_input.returnPressed.connect(self.run_search)
        data_layout.addWidget(self.data_input, 1)
        self.search_stack.addWidget(data_page)

        edge_page = QWidget()
        edge_layout = QHBoxLayout(edge_page)
        edge_layout.setContentsMargins(0, 0, 0, 0)
        edge_layout.setSpacing(6)
        self.signal_combo = QComboBox()
        for name in DIGITAL_SIGNALS:
            self.signal_combo.addItem(name)
        edge_layout.addWidget(self.signal_combo, 1)
        self.search_stack.addWidget(edge_page)

        states_page = QWidget()
        states_layout = QVBoxLayout(states_page)
        states_layout.setContentsMargins(0, 0, 0, 0)
        states_layout.setSpacing(4)
        hint = QLabel("Match when all of these levels are true:")
        hint.setWordWrap(True)
        states_layout.addWidget(hint)

        self.state_rows_host = QWidget()
        self.state_rows_layout = QVBoxLayout(self.state_rows_host)
        self.state_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.state_rows_layout.setSpacing(4)
        self.state_rows_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.state_rows_host)
        scroll.setMinimumHeight(90)
        scroll.setMaximumHeight(140)
        states_layout.addWidget(scroll)

        add_row = QHBoxLayout()
        add_row.addStretch(1)
        self.add_state_button = QPushButton("Add condition")
        self.add_state_button.clicked.connect(lambda: self._add_state_row())
        add_row.addWidget(self.add_state_button)
        states_layout.addLayout(add_row)
        self.search_stack.addWidget(states_page)

        layout.addWidget(self.search_stack)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.run_search)
        action_row.addWidget(self.search_button)
        layout.addLayout(action_row)

        self.search_status = QLabel("")
        self.search_status.setObjectName("panelTitle")
        self.search_status.setWordWrap(True)
        layout.addWidget(self.search_status)
        layout.addWidget(self.search_table, 1)
        return page

    def _add_state_row(
        self,
        signal: str | None = None,
        level: int = 1,
    ) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        signal_combo = QComboBox()
        for name in DIGITAL_SIGNALS:
            signal_combo.addItem(name)
        if signal and signal in DIGITAL_SIGNALS:
            signal_combo.setCurrentText(signal)
        elif self._state_rows:
            # Default to next unused signal when possible.
            used = {combo.currentText() for combo, _, _, _ in self._state_rows}
            for name in DIGITAL_SIGNALS:
                if name not in used:
                    signal_combo.setCurrentText(name)
                    break

        level_combo = QComboBox()
        level_combo.addItem("High (1)", 1)
        level_combo.addItem("Low (0)", 0)
        level_combo.setCurrentIndex(0 if level else 1)
        level_combo.setFixedWidth(90)

        remove_button = QPushButton("×")
        remove_button.setFixedWidth(28)
        remove_button.setToolTip("Remove this condition")
        remove_button.clicked.connect(lambda: self._remove_state_row(row))

        row_layout.addWidget(signal_combo, 1)
        row_layout.addWidget(level_combo, 0)
        row_layout.addWidget(remove_button, 0)

        # Insert before the trailing stretch.
        insert_at = max(0, self.state_rows_layout.count() - 1)
        self.state_rows_layout.insertWidget(insert_at, row)
        self._state_rows.append((signal_combo, level_combo, remove_button, row))
        self._sync_state_remove_buttons()

    def _remove_state_row(self, row_widget: QWidget) -> None:
        for index, entry in enumerate(self._state_rows):
            if entry[3] is row_widget:
                self._state_rows.pop(index)
                break
        self.state_rows_layout.removeWidget(row_widget)
        row_widget.deleteLater()
        if not self._state_rows:
            self._add_state_row()
        self._sync_state_remove_buttons()

    def _sync_state_remove_buttons(self) -> None:
        enabled = len(self._state_rows) > 1
        for _, _, remove_button, _ in self._state_rows:
            remove_button.setEnabled(enabled)

    def _collect_state_constraints(self) -> list[tuple[str, int]]:
        constraints: list[tuple[str, int]] = []
        for signal_combo, level_combo, _, _ in self._state_rows:
            constraints.append(
                (signal_combo.currentText(), int(level_combo.currentData()))
            )
        return constraints

    def _make_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
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
        table.setSpan(0, 0, 1, table.columnCount())
        if table is self.list_table:
            self.delete_marker_button.setEnabled(False)

    def bind_document(self, document: WaveformDocument | None) -> None:
        self._document = document
        self.refresh_markers()
        self.clear_search_results(
            "Search DATA, edges, or multi-signal states."
            if document is not None
            else "Open a waveform tab to search."
        )
        enabled = document is not None
        self.search_mode.setEnabled(enabled)
        self.data_input.setEnabled(enabled)
        self.signal_combo.setEnabled(enabled)
        self.search_button.setEnabled(enabled)
        self.add_state_button.setEnabled(enabled)
        for signal_combo, level_combo, remove_button, _ in self._state_rows:
            signal_combo.setEnabled(enabled)
            level_combo.setEnabled(enabled)
            remove_button.setEnabled(enabled and len(self._state_rows) > 1)

    def clear_search_results(self, message: str) -> None:
        self.search_status.setText("")
        self._show_placeholder(self.search_table, message)

    def refresh_markers(self) -> None:
        self.list_table.clearSpans()
        self.list_table.setRowCount(0)
        self.delete_marker_button.setEnabled(False)

        if self._document is None:
            self._show_placeholder(self.list_table, "Open a log to view markers.")
            return

        rows = marker_rows(self._document.view_state.markers_ns)
        if not rows:
            self._show_placeholder(
                self.list_table,
                "No markers yet — right-click the waveform to add one.",
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

                time_item = QTableWidgetItem(self._format_time(float(row["time_ns"])))
                time_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                diff = row["diff"]
                if isinstance(diff, str):
                    diff_text = diff
                else:
                    diff_text = self._format_time(float(diff))
                diff_item = QTableWidgetItem(diff_text)
                diff_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                self.list_table.setItem(row_index, 0, mark_item)
                self.list_table.setItem(row_index, 1, sample_item)
                self.list_table.setItem(row_index, 2, time_item)
                self.list_table.setItem(row_index, 3, diff_item)
        finally:
            self._updating = False

        self._update_delete_button()

    def run_search(self) -> None:
        if self._document is None:
            self.clear_search_results("Open a waveform tab to search.")
            return

        mode = self.search_mode.currentData()
        timeline = self._document.timeline
        try:
            if mode == "data":
                value = parse_data_byte(self.data_input.text())
                hits = search_data_value(timeline, value)
                query_label = f"DATA={value:02X}"
            elif mode == "states":
                constraints = self._collect_state_constraints()
                hits = search_signal_states(timeline, constraints)
                query_label = " / ".join(
                    f"{signal}={'H' if level else 'L'}" for signal, level in constraints
                )
            else:
                signal = self.signal_combo.currentText()
                hits = search_edge(timeline, signal, mode)
                query_label = f"{signal} {mode}"
        except ValueError as exc:
            self.clear_search_results(str(exc))
            return

        if not hits:
            self.clear_search_results(f"No matches for {query_label}.")
            return

        rows = search_hit_rows(hits)
        self.search_table.clearSpans()
        self.search_table.setRowCount(len(rows))
        self._updating = True
        try:
            for row_index, row in enumerate(rows):
                index_item = QTableWidgetItem(str(row["index"]))
                index_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                index_item.setData(Qt.ItemDataRole.UserRole, float(row["time_ns"]))

                match_item = QTableWidgetItem(str(row["match"]))
                match_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )

                time_item = QTableWidgetItem(self._format_time(float(row["time_ns"])))
                time_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                diff = row["diff"]
                if isinstance(diff, str):
                    diff_text = diff
                else:
                    diff_text = self._format_time(float(diff))
                diff_item = QTableWidgetItem(diff_text)
                diff_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                self.search_table.setItem(row_index, 0, index_item)
                self.search_table.setItem(row_index, 1, match_item)
                self.search_table.setItem(row_index, 2, time_item)
                self.search_table.setItem(row_index, 3, diff_item)
        finally:
            self._updating = False

        self.search_status.setText(f"{len(hits)} match{'es' if len(hits) != 1 else ''}")
        self.search_table.selectRow(0)
        self.search_table.setFocus(Qt.FocusReason.OtherFocusReason)

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

    @staticmethod
    def _format_time(time_ns: float) -> str:
        if abs(time_ns) >= 1000.0:
            return f"{time_ns:.3e}"
        return f"{time_ns:.3f}"

    def _on_search_mode_changed(self, index: int) -> None:
        mode = self.search_mode.itemData(index)
        if mode == "data":
            self.search_stack.setCurrentIndex(0)
        elif mode == "states":
            self.search_stack.setCurrentIndex(2)
        else:
            self.search_stack.setCurrentIndex(1)

    def _selected_marker_time(self) -> float | None:
        return self._selected_table_time(self.list_table)

    def _selected_search_time(self) -> float | None:
        return self._selected_table_time(self.search_table)

    def _selected_table_time(self, table: QTableWidget) -> float | None:
        items = table.selectedItems()
        if not items:
            return None
        item = table.item(items[0].row(), 0)
        if item is None:
            return None
        stored = item.data(Qt.ItemDataRole.UserRole)
        if stored is None:
            return None
        return float(stored)

    def _on_search_selection(self) -> None:
        if self._updating:
            return
        time_ns = self._selected_search_time()
        if time_ns is None:
            return
        self.search_hit_selected.emit(time_ns)

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
