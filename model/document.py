"""Document model for an opened log / waveform tab."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from model.timeline import BusSegment, Edge, Timeline


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
    "REP",
    "DQSP",
    "DQSN",
    "RB0",
    "RB1",
    "WP",
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
    "WP": "#fca5a5",
    "REN": "#60a5fa",
    "REP": "#93c5fd",
    "DQSP": "#e2e8f0",
    "DQSN": "#94a3b8",
    "RB0": "#34d399",
    "RB1": "#2dd4bf",
    "DATA": "#c4b5fd",
}

# Digital edge values: 0/1 driven, LEVEL_Z = undriven / High-Z (drawn mid-track).
LEVEL_Z = 2

# Active-low pins idle HIGH; active-high pins idle LOW.
# REN/REP park as true differential complements. DQSP/DQSN idle High-Z.
# RB ready = HIGH.
INACTIVE_LEVELS: dict[str, int] = {
    "CE0": 1,  # CE# active low
    "CE1": 1,
    "CE2": 1,
    "CE3": 1,
    "CLE": 0,  # active high
    "ALE": 0,  # active high
    "WEN": 1,  # WE# active low
    "WP": 1,  # WP# active low (protect asserted low; idle = unprotected high)
    "REN": 1,  # RE# inactive high
    "REP": 0,  # differential complement of REN
    "DQSP": LEVEL_Z,
    "DQSN": LEVEL_Z,
    "RB0": 1,  # ready (busy is active low)
    "RB1": 1,
}

DIGITAL_SIGNALS: list[str] = [s for s in DEFAULT_SIGNALS if s != "DATA"]

IDLE_DURATION_NS = 200.0


@dataclass
class ViewState:
    """Per-tab pan / zoom / cursor state."""

    zoom_ps_per_px: float = 217.1
    pan_ns: float = 0.0
    cursor_ns: float | None = None
    markers_ns: list[float] = field(default_factory=list)


@dataclass
class WaveformDocument:
    """One opened log (or demo), shown as a single tab."""

    path: Path | None
    title: str
    signals: list[str] = field(default_factory=lambda: list(DEFAULT_SIGNALS))
    view_state: ViewState = field(default_factory=ViewState)
    timeline: Timeline = field(default_factory=Timeline)
    loaded: bool = False
    note: str = ""

    def current_values(self) -> dict[str, str]:
        """Values shown in the signal list for the cursor / start of view."""
        t = self.view_state.cursor_ns
        if t is None:
            t = self.view_state.pan_ns
        values: dict[str, str] = {}
        for name in self.signals:
            if name == "DATA":
                values[name] = self._data_value_at(t)
            else:
                default = INACTIVE_LEVELS.get(name, 0)
                level = self.timeline.level_at(name, t, default)
                values[name] = "Z" if level == LEVEL_Z else str(level)
        return values

    def _data_value_at(self, time_ns: float) -> str:
        """DATA value at ``time_ns`` for the signal list; ``Z`` when undriven."""
        for seg in self.timeline.bus_segments:
            if seg.time_ns <= time_ns < seg.time_ns + seg.duration_ns:
                hex_val = (seg.value_hex or "").upper()
                if hex_val in ("", "ZZ", "Z"):
                    return "Z"
                return seg.value_hex
        return "Z"

    @classmethod
    def from_path(cls, path: Path) -> WaveformDocument:
        return cls(
            path=path,
            title=path.name,
            loaded=True,
            note="Log parsing not implemented yet.",
            timeline=Timeline(t_min_ns=0.0, t_max_ns=IDLE_DURATION_NS),
        )

    @classmethod
    def idle_demo(cls, index: int = 1) -> WaveformDocument:
        """Temporary demo: full §5.1 Read Cmd Issue + Data Out."""
        from decode.nphy_packets import draw_read_sequence
        from model.timing import DEFAULT_TIMING

        timeline = Timeline(t_min_ns=0.0, t_max_ns=0.0)
        draw_read_sequence(
            timeline,
            start_ns=10.0,
            lun=0,
            t_r_ns=DEFAULT_TIMING.t_r_ns,  # 24.1 µs
            timing=DEFAULT_TIMING,
        )
        return cls(
            path=None,
            title=f"Idle {index}",
            loaded=True,
            note=(
                "TEMP demo — §5.1 Read Cmd Issue + Data Out "
                f"(tR={DEFAULT_TIMING.t_r_ns / 1000:g} µs)."
            ),
            timeline=timeline,
            view_state=ViewState(zoom_ps_per_px=1000.0, pan_ns=0.0),
        )


def build_idle_timeline(duration_ns: float = IDLE_DURATION_NS) -> Timeline:
    edges = [
        Edge(time_ns=0.0, signal=name, value=INACTIVE_LEVELS[name])
        for name in DIGITAL_SIGNALS
    ]
    bus = [
        BusSegment(
            time_ns=0.0,
            duration_ns=duration_ns,
            value_hex="ZZ",
            label="High-Z",
        )
    ]
    return Timeline(
        edges=edges,
        bus_segments=bus,
        t_min_ns=0.0,
        t_max_ns=duration_ns,
    )
