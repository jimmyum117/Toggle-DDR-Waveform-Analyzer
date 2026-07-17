"""Search helpers for DATA values, edges, and multi-signal states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from model.document import DIGITAL_SIGNALS, INACTIVE_LEVELS
from model.timeline import Timeline

EdgeKind = Literal["rising", "falling"]


@dataclass(frozen=True)
class SearchHit:
    """One search match on the waveform timeline."""

    kind: str  # "data" | "rising" | "falling" | "states"
    time_ns: float
    label: str
    signal: str | None = None
    value_hex: str | None = None
    end_ns: float | None = None


def parse_data_byte(text: str) -> int:
    """Parse a DATA byte from forms like ``0xFF``, ``FF``, ``90``, or ``255``."""
    raw = text.strip().replace("_", "").replace(" ", "")
    if not raw:
        raise ValueError("Enter a DATA value (e.g. 0xFF or FF)")

    lowered = raw.lower()
    if lowered.startswith("0x"):
        return int(raw, 16) & 0xFF

    hex_chars = set("0123456789abcdefABCDEF")
    if all(ch in hex_chars for ch in raw):
        # Prefer hex for typical DQ bytes (1–2 digits, or any A–F present).
        if len(raw) <= 2 or any(ch in "abcdefABCDEF" for ch in raw):
            return int(raw, 16) & 0xFF

    if raw.isdigit():
        value = int(raw, 10)
        if not 0 <= value <= 255:
            raise ValueError("Decimal DATA value must be 0..255")
        return value

    return int(raw, 16) & 0xFF


def normalize_data_hex(value: int) -> str:
    return f"{value & 0xFF:02X}"


def format_state_constraints(constraints: Sequence[tuple[str, int]]) -> str:
    """Human-readable constraint list, e.g. ``CLE=H ALE=L CE0=L``."""
    parts: list[str] = []
    for signal, level in constraints:
        parts.append(f"{signal}={'H' if level else 'L'}")
    return " ".join(parts)


def search_data_value(timeline: Timeline, value: int) -> list[SearchHit]:
    """Find DATA bus segments whose hex byte matches ``value``."""
    wanted = normalize_data_hex(value)
    hits: list[SearchHit] = []
    for seg in sorted(timeline.bus_segments, key=lambda s: s.time_ns):
        if seg.value_hex.upper() != wanted:
            continue
        label = seg.label or f"DATA={wanted}"
        hits.append(
            SearchHit(
                kind="data",
                time_ns=seg.time_ns,
                label=label,
                signal="DATA",
                value_hex=wanted,
                end_ns=seg.time_ns + seg.duration_ns,
            )
        )
    return hits


def search_edge(
    timeline: Timeline,
    signal: str,
    edge: EdgeKind,
) -> list[SearchHit]:
    """Find rising (0→1) or falling (1→0) transitions on ``signal``."""
    if signal not in DIGITAL_SIGNALS:
        raise ValueError(f"Unknown digital signal: {signal}")

    edges = timeline.edges_for(signal)
    if not edges:
        return []

    default = INACTIVE_LEVELS.get(signal, 0)
    prev = default
    # Treat the first stored edge as baseline unless it changes level.
    if edges and abs(edges[0].time_ns) <= 1e-15:
        prev = edges[0].value
        start = 1
    else:
        start = 0

    target = 1 if edge == "rising" else 0
    hits: list[SearchHit] = []
    for item in edges[start:]:
        if item.value == prev:
            continue
        if item.value == target and prev == 1 - target:
            verb = "rising" if edge == "rising" else "falling"
            hits.append(
                SearchHit(
                    kind=edge,
                    time_ns=item.time_ns,
                    label=f"{signal} {verb}",
                    signal=signal,
                )
            )
        prev = item.value
    return hits


def search_signal_states(
    timeline: Timeline,
    constraints: Sequence[tuple[str, int]],
) -> list[SearchHit]:
    """Find times when every listed signal holds the requested level.

    ``constraints`` is a sequence of ``(signal, level)`` with level 0/1.
    Each contiguous matching interval contributes one hit at the instant the
    combined state becomes true.
    """
    if not constraints:
        raise ValueError("Add at least one signal state condition")

    normalized: list[tuple[str, int]] = []
    seen: set[str] = set()
    for signal, level in constraints:
        if signal not in DIGITAL_SIGNALS:
            raise ValueError(f"Unknown digital signal: {signal}")
        if level not in (0, 1):
            raise ValueError(f"Level for {signal} must be 0 or 1")
        if signal in seen:
            raise ValueError(f"Duplicate condition for {signal}")
        seen.add(signal)
        normalized.append((signal, int(level)))

    event_times = {0.0, float(timeline.t_min_ns), float(timeline.t_max_ns)}
    for signal, _ in normalized:
        for edge in timeline.edges_for(signal):
            event_times.add(float(edge.time_ns))
    times = sorted(event_times)

    label = format_state_constraints(normalized)
    hits: list[SearchHit] = []
    matching = False

    for index, time_ns in enumerate(times):
        ok = all(
            timeline.level_at(signal, time_ns, INACTIVE_LEVELS.get(signal, 0)) == level
            for signal, level in normalized
        )
        if ok and not matching:
            matching = True
            hits.append(
                SearchHit(
                    kind="states",
                    time_ns=time_ns,
                    label=label,
                    signal=normalized[0][0],
                )
            )
        elif not ok and matching:
            if hits:
                hits[-1] = SearchHit(
                    kind="states",
                    time_ns=hits[-1].time_ns,
                    label=hits[-1].label,
                    signal=hits[-1].signal,
                    end_ns=time_ns,
                )
            matching = False

        if matching and index == len(times) - 1 and hits:
            hits[-1] = SearchHit(
                kind="states",
                time_ns=hits[-1].time_ns,
                label=hits[-1].label,
                signal=hits[-1].signal,
                end_ns=time_ns,
            )

    return hits


def search_hit_rows(hits: list[SearchHit]) -> list[dict[str, float | int | str]]:
    """Build Search View rows: #, Match, Time(ns), Diff."""
    rows: list[dict[str, float | int | str]] = []
    prev: float | None = None
    for index, hit in enumerate(hits, start=1):
        if prev is None:
            diff: float | str = "—"
        else:
            diff = hit.time_ns - prev
        match = hit.label
        if hit.end_ns is not None and hit.kind == "states":
            match = f"{hit.label} [{hit.time_ns:.3g}→{hit.end_ns:.3g}]"
        rows.append(
            {
                "index": index,
                "match": match,
                "time_ns": hit.time_ns,
                "diff": diff,
            }
        )
        prev = hit.time_ns
    return rows
