"""Left sidebar: signal / pin name list aligned with waveform tracks."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from model.document import SIGNAL_COLORS, WaveformDocument
from ui.layout_metrics import RULER_HEIGHT, track_height_for


class SignalListWidget(QWidget):
    """Shows pin names aligned 1:1 with waveform track rows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("signalList")
        self.setMinimumWidth(160)
        self.setMaximumWidth(280)
        self._document: WaveformDocument | None = None

        # Matches the QTabBar height so content lines up with the tab page.
        self._tab_spacer = QWidget()
        self._tab_spacer.setObjectName("signalTabSpacer")
        self._tab_spacer.setFixedHeight(0)
        self._tab_label = QLabel("Signals")
        self._tab_label.setObjectName("panelTitle")
        self._tab_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        spacer_layout = QVBoxLayout(self._tab_spacer)
        spacer_layout.setContentsMargins(10, 0, 8, 0)
        spacer_layout.setSpacing(0)
        spacer_layout.addWidget(self._tab_label)

        # Explicit ruler-height band (do not rely on QTableWidget header sizing).
        self._ruler_header = QWidget()
        self._ruler_header.setObjectName("signalRulerHeader")
        self._ruler_header.setFixedHeight(RULER_HEIGHT)
        ruler_layout = QHBoxLayout(self._ruler_header)
        ruler_layout.setContentsMargins(10, 0, 8, 0)
        ruler_layout.setSpacing(8)
        pin_hdr = QLabel("Pin")
        pin_hdr.setObjectName("signalColumnHeader")
        val_hdr = QLabel("Val")
        val_hdr.setObjectName("signalColumnHeader")
        val_hdr.setFixedWidth(36)
        val_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cnt_hdr = QLabel("Cnt")
        cnt_hdr.setObjectName("signalColumnHeader")
        cnt_hdr.setFixedWidth(36)
        cnt_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ruler_layout.addWidget(pin_hdr, 1)
        ruler_layout.addWidget(val_hdr, 0)
        ruler_layout.addWidget(cnt_hdr, 0)

        self.table = QTableWidget(0, 3)
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 36)
        self.table.setColumnWidth(2, 36)
        self.table.verticalHeader().setMinimumSectionSize(0)
        self.table.verticalHeader().setDefaultSectionSize(track_height_for(""))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._tab_spacer)
        layout.addWidget(self._ruler_header)
        layout.addWidget(self.table)
        layout.addStretch(1)

    def set_top_spacer_height(self, height: int) -> None:
        """Set the blank region above the ruler header (pin/track alignment)."""
        height = max(0, int(height))
        if self._tab_spacer.height() != height:
            self._tab_spacer.setFixedHeight(height)

    def top_spacer_height(self) -> int:
        return self._tab_spacer.height()

    def bind_document(self, document: WaveformDocument | None) -> None:
        self._document = document
        self.table.setRowCount(0)
        if document is None:
            self.table.setFixedHeight(0)
            return

        total_height = 0
        for name in document.signals:
            row = self.table.rowCount()
            self.table.insertRow(row)
            row_h = track_height_for(name)
            self.table.setRowHeight(row, row_h)
            total_height += row_h

            pin_item = QTableWidgetItem(name)
            color = SIGNAL_COLORS.get(name, "#e2e8f0")
            pin_item.setForeground(QBrush(QColor(color)))
            pin_item.setFlags(pin_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            pin_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )

            val_item = QTableWidgetItem("0")
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            cnt_item = QTableWidgetItem("0")
            cnt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.table.setItem(row, 0, pin_item)
            self.table.setItem(row, 1, val_item)
            self.table.setItem(row, 2, cnt_item)

        # Rows only — ruler band is a separate widget above the table.
        self.table.setFixedHeight(total_height)
        self.refresh_values()

    def refresh_values(self) -> None:
        """Update the Val column from the cursor (or pan) time on the timeline."""
        document = getattr(self, "_document", None)
        if document is None:
            return
        values = document.current_values()
        for row, name in enumerate(document.signals):
            item = self.table.item(row, 1)
            if item is not None:
                item.setText(values.get(name, "0"))
