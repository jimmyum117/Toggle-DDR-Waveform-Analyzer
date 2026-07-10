"""Timeline primitives for waveform rendering.

Log parsing will populate these later; for now demos build idle timelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Edge:
    """Digital signal transition (or initial level at time_ns)."""

    time_ns: float
    signal: str
    value: int  # 0 or 1


@dataclass
class BusSegment:
    """Multi-bit DATA value spanning [time_ns, time_ns + duration_ns)."""

    time_ns: float
    duration_ns: float
    value_hex: str
    label: str | None = None


@dataclass
class Timeline:
    edges: list[Edge] = field(default_factory=list)
    bus_segments: list[BusSegment] = field(default_factory=list)
    t_min_ns: float = 0.0
    t_max_ns: float = 200.0

    def edges_for(self, signal: str) -> list[Edge]:
        return sorted(
            (e for e in self.edges if e.signal == signal),
            key=lambda e: e.time_ns,
        )

    def level_at(self, signal: str, time_ns: float, default: int = 0) -> int:
        level = default
        for edge in self.edges_for(signal):
            if edge.time_ns <= time_ns:
                level = edge.value
            else:
                break
        return level
