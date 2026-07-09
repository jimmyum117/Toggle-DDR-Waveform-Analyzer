"""Left sidebar: signal / pin name list aligned with waveform tracks."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QLabel,
)

from model.document import SIGNAL_COLORS, WaveformDocument


class SignalListWidget(QWidget):
    """Shows pin names and placeholder current values for the active document."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("signalList")
        self.setMinimumWidth(160)
        self.setMaximumWidth(280)

        title = QLabel("Signals")
        title.setObjectName("panelTitle")

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Pin", "Val", "Cnt"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(28)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(self.table)

    def bind_document(self, document: WaveformDocument | None) -> None:
        self.table.setRowCount(0)
        if document is None:
            return

        for name in document.signals:
            row = self.table.rowCount()
            self.table.insertRow(row)

            pin_item = QTableWidgetItem(name)
            color = SIGNAL_COLORS.get(name, "#e2e8f0")
            pin_item.setForeground(QBrush(QColor(color)))
            pin_item.setFlags(pin_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            val_item = QTableWidgetItem("0")
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            cnt_item = QTableWidgetItem("0")
            cnt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.table.setItem(row, 0, pin_item)
            self.table.setItem(row, 1, val_item)
            self.table.setItem(row, 2, cnt_item)
