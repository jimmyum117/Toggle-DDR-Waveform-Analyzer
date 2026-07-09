"""Document model for an opened log / waveform tab.

Timeline decoding and log parsing are intentionally omitted for now.
Each open file is represented as a WaveformDocument with view state only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# Default Toggle DDR / ONFI-style pin order for the signal list UI.
DEFAULT_SIGNALS: list[str] = [
    "CE0",
    "CE1",
    "CE2",
    "CE3",
    "CLE",
    "ALE",
    "WEN",
    "REN",
    "DQSP",
    "DQSN",
    "RB0",
    "RB1",
    "DATA",
]

# Distinct colors for each track (dark-theme friendly).
SIGNAL_COLORS: dict[str, str] = {
    "CE0": "#4ade80",
    "CE1": "#22d3ee",
    "CE2": "#a78bfa",
    "CE3": "#f472b6",
    "CLE": "#fbbf24",
    "ALE": "#fb923c",
    "WEN": "#f87171",
    "REN": "#60a5fa",
    "DQSP": "#e2e8f0",
    "DQSN": "#94a3b8",
    "RB0": "#34d399",
    "RB1": "#2dd4bf",
    "DATA": "#c4b5fd",
}


@dataclass
class ViewState:
    """Per-tab pan / zoom / cursor state (logic filled in later)."""

    zoom_ps_per_px: float = 217.1
    pan_ns: float = 0.0
    cursor_ns: float | None = None
    markers_ns: list[float] = field(default_factory=list)


@dataclass
class WaveformDocument:
    """One opened log, shown as a single tab."""

    path: Path | None
    title: str
    signals: list[str] = field(default_factory=lambda: list(DEFAULT_SIGNALS))
    view_state: ViewState = field(default_factory=ViewState)
    # Placeholder until a real parser exists.
    loaded: bool = False
    note: str = "Waveform drawing and log parsing not implemented yet."

    @classmethod
    def from_path(cls, path: Path) -> WaveformDocument:
        return cls(path=path, title=path.name, loaded=True)

    @classmethod
    def untitled(cls, index: int = 1) -> WaveformDocument:
        return cls(path=None, title=f"Untitled {index}", loaded=False)
