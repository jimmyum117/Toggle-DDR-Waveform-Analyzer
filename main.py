"""
Toggle DDR Waveform Analyzer
----------------------------
UI shell for viewing Toggle DDR pin waveforms from log files.

Waveform drawing, timing, and log parsing are intentionally stubbed for later.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


DARK_QSS = """
QWidget {
    background-color: #0f172a;
    color: #e2e8f0;
    font-size: 13px;
}
QMainWindow, QStatusBar {
    background-color: #0f172a;
}
QMenuBar {
    background-color: #111827;
    color: #e5e7eb;
    padding: 2px;
}
QMenuBar::item:selected {
    background-color: #1f2937;
}
QMenu {
    background-color: #111827;
    border: 1px solid #374151;
}
QMenu::item:selected {
    background-color: #2563eb;
}
QToolBar {
    background-color: #111827;
    border-bottom: 1px solid #1f2937;
    spacing: 6px;
    padding: 4px;
}
QToolButton {
    background-color: #1f2937;
    color: #f3f4f6;
    border: 1px solid #374151;
    border-radius: 4px;
    padding: 6px 12px;
}
QToolButton:hover {
    background-color: #374151;
}
QToolButton:pressed {
    background-color: #2563eb;
}
QToolButton:disabled {
    color: #6b7280;
    background-color: #111827;
}
QSplitter::handle {
    background-color: #1f2937;
    width: 2px;
}
QTabWidget::pane {
    border: 1px solid #1f2937;
    background-color: #0a0a0a;
}
QTabBar::tab {
    background-color: #111827;
    color: #9ca3af;
    padding: 8px 14px;
    border: 1px solid #1f2937;
    border-bottom: none;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #0a0a0a;
    color: #f9fafb;
}
QTabBar::tab:hover {
    color: #e5e7eb;
}
QHeaderView::section {
    background-color: #111827;
    color: #9ca3af;
    padding: 4px;
    border: none;
    border-bottom: 1px solid #1f2937;
}
QTableWidget {
    background-color: #0b1220;
    alternate-background-color: #111827;
    gridline-color: #1f2937;
    border: 1px solid #1f2937;
    selection-background-color: #1d4ed8;
}
QLineEdit {
    background-color: #111827;
    border: 1px solid #374151;
    border-radius: 4px;
    padding: 6px;
}
QLabel#panelTitle {
    font-weight: 600;
    color: #cbd5e1;
    letter-spacing: 0.3px;
}
QStatusBar {
    color: #94a3b8;
}
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Toggle DDR Waveform Analyzer")
    app.setOrganizationName("EEUM")
    app.setStyle("Fusion")
    app.setFont(QFont(".AppleSystemUIFont", 12))
    if not app.font().exactMatch():
        app.setFont(QFont("Helvetica Neue", 12))
    app.setStyleSheet(DARK_QSS)

    window = MainWindow()
    window.show()

    # Optional: open paths passed on the command line as tabs.
    for arg in sys.argv[1:]:
        path = Path(arg)
        if path.is_file():
            window.open_path(path)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
