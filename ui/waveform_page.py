"""One tab page: waveform viewport for a single opened log document."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from model.document import WaveformDocument
from ui.waveform_view import WaveformView


class WaveformPage(QWidget):
    """Contents of a single QTabWidget page."""

    def __init__(self, document: WaveformDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self.waveform_view = WaveformView(document)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.waveform_view)
