"""Marker helpers for waveform view state."""

from __future__ import annotations


def sorted_markers(markers_ns: list[float]) -> list[float]:
    return sorted(markers_ns)


def marker_rows(markers_ns: list[float]) -> list[dict[str, float | int | str]]:
    """Build List View rows: Mark, Sample, Time(ns), Diff."""
    rows: list[dict[str, float | int | str]] = []
    ordered = sorted_markers(markers_ns)
    prev: float | None = None
    for index, time_ns in enumerate(ordered, start=1):
        if prev is None:
            diff: float | str = "—"
        else:
            diff = time_ns - prev
        rows.append(
            {
                "mark": index,
                "sample": index - 1,
                "time_ns": time_ns,
                "diff": diff,
            }
        )
        prev = time_ns
    return rows


def add_marker(markers_ns: list[float], time_ns: float, *, snap_eps_ns: float = 0.001) -> bool:
    """Append a marker if it is not essentially a duplicate. Returns True if added."""
    for existing in markers_ns:
        if abs(existing - time_ns) <= snap_eps_ns:
            return False
    markers_ns.append(time_ns)
    return True
