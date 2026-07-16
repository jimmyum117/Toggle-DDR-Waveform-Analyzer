"""NPHY packet → Toggle DDR pin timeline synthesizers.

These helpers do not parse logs. A future log parser can call them in opcode
order to grow a :class:`model.timeline.Timeline` that the waveform viewport
already knows how to render.

Sources (``2_nphy_packet_study_eng.md``):
  - §2.1 Used/Unused Mapping — opcodes
  - §3 Used packet details — field settings / pin intent
  - §4.1 databook timing ↔ FW settings — tCS, tWP, tWH, …
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from model.document import DIGITAL_SIGNALS, INACTIVE_LEVELS
from model.timeline import BusSegment, Edge, Timeline
from model.timing import DEFAULT_TIMING, NphyTiming

# Runtime opcodes as used in lhotse macros (BASIC 0x00–0x0F, EXTEND 0x10–0x1F).
OPC_E_ASSERT_CE = 0x10  # EXTEND 0
OPC_E_WRITE_CMD = 0x11  # EXTEND 1
OPC_E_WRITE_ADDR = 0x12  # EXTEND 2
OPC_E_READ_DATA_DMA = 0x17  # EXTEND 7
OPC_E_RPIO_COMPARE_REPEAT = 0x19  # EXTEND 9
OPC_B_NOP = 0x02  # BASIC 2
OPC_E_TIMER_CTRL = 0x1A  # EXTEND A
OPC_E_DEASSERT_ALL_CE = 0x1D  # EXTEND D

# E_TIMER_CTRL.NPHY_OP[31:30]
NPHY_OP_TIMER_NOP = 0b00
NPHY_OP_DEASSERT_CE_WDMA = 0b01  # used by lld_nphy_set_timer_and_deassert_ce
NPHY_OP_DEASSERT_CE_RDMA = 0b10
NPHY_OP_DEASSERT_CE_IMMEDIATE = 0b11

# E_TIMER_CTRL.Timer OP[6:5]
TIMER_OP_START = 0
TIMER_OP_EXPIRE = 1

CE_SIGNALS: tuple[str, ...] = ("CE0", "CE1", "CE2", "CE3")


@dataclass(frozen=True)
class PacketDrawResult:
    """Result of appending one NPHY packet's pin activity to a timeline."""

    opcode: int
    name: str
    start_ns: float
    end_ns: float
    lun: int | None = None
    ce_signal: str | None = None
    nand_cmd: int | None = None
    nand_addr: int | None = None
    cycles: int | None = None
    duration_ns: float | None = None
    ticks: int | None = None
    nphy_op: int | None = None
    timer_op: int | None = None
    cpl: int | None = None
    deassert_ce: bool | None = None
    byte_count: int | None = None
    free_pause_valid: bool | None = None
    drop: bool | None = None
    compare_value: int | None = None
    dq_mask: int | None = None
    repeat_count: int | None = None
    matched: bool | None = None
    phyupd_chk: int | None = None


def ensure_idle_baseline(timeline: Timeline, at_ns: float = 0.0) -> None:
    """Seed inactive levels for every digital pin if the timeline has no edges yet."""
    for signal in DIGITAL_SIGNALS:
        if timeline.edges_for(signal):
            continue
        timeline.edges.append(
            Edge(time_ns=at_ns, signal=signal, value=INACTIVE_LEVELS[signal])
        )
    timeline.t_min_ns = min(timeline.t_min_ns, at_ns)
    timeline.t_max_ns = max(timeline.t_max_ns, at_ns)


def _append_edge(timeline: Timeline, time_ns: float, signal: str, value: int) -> None:
    """Append a transition only when it changes the level at time_ns."""
    default = INACTIVE_LEVELS.get(signal, 0)
    for i in range(len(timeline.edges) - 1, -1, -1):
        edge = timeline.edges[i]
        if edge.signal != signal:
            continue
        if abs(edge.time_ns - time_ns) <= 1e-15:
            if edge.value == value:
                return
            timeline.edges[i] = Edge(time_ns=time_ns, signal=signal, value=value)
            timeline.t_max_ns = max(timeline.t_max_ns, time_ns)
            return
        break

    prior = timeline.level_at(signal, time_ns - 1e-12, default)
    if prior == value and timeline.edges_for(signal):
        return
    timeline.edges.append(Edge(time_ns=time_ns, signal=signal, value=value))
    timeline.t_max_ns = max(timeline.t_max_ns, time_ns)


def _append_data(
    timeline: Timeline,
    time_ns: float,
    duration_ns: float,
    value: int,
    *,
    label: str | None = None,
) -> None:
    timeline.bus_segments.append(
        BusSegment(
            time_ns=time_ns,
            duration_ns=max(0.0, duration_ns),
            value_hex=f"{value & 0xFF:02X}",
            label=label,
        )
    )
    timeline.t_max_ns = max(timeline.t_max_ns, time_ns + max(0.0, duration_ns))


def _require_start_ns(start_ns: float) -> None:
    if start_ns < 0:
        raise ValueError("start_ns must be >= 0")


def lun_to_ce_signal(lun: int) -> str:
    """Map LUN number to CEx track (LUN-based path, ``ENABLE_NON_POWER_OF_TWO == 0``)."""
    if not 0 <= lun < len(CE_SIGNALS):
        raise ValueError(f"lun must be 0..{len(CE_SIGNALS) - 1}, got {lun}")
    return CE_SIGNALS[lun]


def lun_to_rb_signal(lun: int) -> str:
    """Map LUN to R/B# track (RB0 for even LUN, RB1 for odd)."""
    if lun < 0:
        raise ValueError(f"lun must be >= 0, got {lun}")
    return "RB1" if (lun & 1) else "RB0"


def _set_rb_busy(timeline: Timeline, time_ns: float, lun: int, busy: bool) -> None:
    """Drive NAND R/B# (active-low busy)."""
    _append_edge(timeline, time_ns, lun_to_rb_signal(lun), 0 if busy else 1)


def _deassert_all_ce(timeline: Timeline, time_ns: float) -> None:
    for ce in CE_SIGNALS:
        _append_edge(timeline, time_ns, ce, 1)


def _append_bus_value(
    timeline: Timeline,
    time_ns: float,
    duration_ns: float,
    value_hex: str,
    *,
    label: str | None = None,
) -> None:
    timeline.bus_segments.append(
        BusSegment(
            time_ns=time_ns,
            duration_ns=max(0.0, duration_ns),
            value_hex=value_hex,
            label=label,
        )
    )
    timeline.t_max_ns = max(timeline.t_max_ns, time_ns + max(0.0, duration_ns))


def _append_read_transfer(
    timeline: Timeline,
    *,
    start_ns: float,
    byte_count: int,
    data: Sequence[int] | None,
    label: str,
    timing: NphyTiming,
) -> float:
    """Draw configured RE/DQS activity for one Toggle-DDR read transfer."""
    if byte_count <= 0:
        raise ValueError("byte_count must be > 0")
    if data is not None and len(data) < byte_count:
        raise ValueError("data must contain at least byte_count entries")

    first_re_ns = start_ns + timing.t_cr_ns
    data_start_ns = first_re_ns + max(timing.t_rpre_ns, timing.t_dqsre_ns)
    beat_ns = timing.t_rc_ns / 2.0
    cycle_count = (byte_count + 1) // 2

    # RE is differential while active. Both tracks return to their configured
    # idle levels after the transfer.
    for cycle in range(cycle_count):
        cycle_ns = first_re_ns + cycle * timing.t_rc_ns
        _append_edge(timeline, cycle_ns, "REN", 0)
        _append_edge(timeline, cycle_ns, "REP", 1)
        _append_edge(timeline, cycle_ns + timing.t_rp_ns, "REN", 1)
        _append_edge(timeline, cycle_ns + timing.t_rp_ns, "REP", 0)

    for index in range(byte_count):
        beat_start_ns = data_start_ns + index * beat_ns
        dqs_p = index & 1
        _append_edge(timeline, beat_start_ns, "DQSP", dqs_p)
        _append_edge(timeline, beat_start_ns, "DQSN", 1 - dqs_p)
        value = "XX" if data is None else f"{int(data[index]) & 0xFF:02X}"
        _append_bus_value(
            timeline,
            beat_start_ns,
            beat_ns,
            value,
            label=label if index == 0 else None,
        )

    transfer_end_ns = data_start_ns + byte_count * beat_ns
    end_ns = transfer_end_ns + max(timing.t_rpst_ns, timing.t_rpsth_ns)
    _append_edge(timeline, end_ns, "REN", INACTIVE_LEVELS["REN"])
    _append_edge(timeline, end_ns, "REP", INACTIVE_LEVELS["REP"])
    _append_edge(timeline, end_ns, "DQSP", INACTIVE_LEVELS["DQSP"])
    _append_edge(timeline, end_ns, "DQSN", INACTIVE_LEVELS["DQSN"])
    timeline.t_max_ns = max(timeline.t_max_ns, end_ns)
    return end_ns


def _write_latch_cycle(
    timeline: Timeline,
    *,
    start_ns: float,
    cle: int,
    ale: int,
    data_byte: int,
    data_label: str,
    timing: NphyTiming,
) -> float:
    """Common CLE/ALE + WE# pulse for command/address latch cycles.

    Approximate SDR latch (§3 address-latch figure / tWP·tWH·tCAS·tCAH):
      - Raise CLE or ALE, present DQ byte
      - After tCAS, pulse WE# low for tWP then high
      - After tCAH/tCALH, drop CLE/ALE and release DQ
    """
    t_cas = timing.t_cas_ns
    t_wp = timing.t_wp_ns
    t_hold = max(timing.t_cah_ns, timing.t_calh_ns)

    t_we_fall = start_ns + t_cas
    t_we_rise = t_we_fall + t_wp
    t_end = t_we_rise + t_hold

    _append_edge(timeline, start_ns, "CLE", 1 if cle else 0)
    _append_edge(timeline, start_ns, "ALE", 1 if ale else 0)
    # WE# idle high; keep REN idle.
    _append_edge(timeline, start_ns, "WEN", 1)
    _append_data(
        timeline,
        start_ns,
        t_end - start_ns,
        data_byte,
        label=data_label,
    )

    _append_edge(timeline, t_we_fall, "WEN", 0)
    _append_edge(timeline, t_we_rise, "WEN", 1)

    _append_edge(timeline, t_end, "CLE", 0)
    _append_edge(timeline, t_end, "ALE", 0)

    timeline.t_max_ns = max(timeline.t_max_ns, t_end)
    return t_end


def draw_e_assert_ce(
    timeline: Timeline,
    *,
    start_ns: float,
    lun: int = 0,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Append pin activity for NPHY ``E_ASSERT_CE`` (EXTEND opcode 0 / 0x10)."""
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    ce_signal = lun_to_ce_signal(lun)
    _append_edge(timeline, start_ns, ce_signal, 0)

    end_ns = start_ns + timing.t_cs_ns
    timeline.t_max_ns = max(timeline.t_max_ns, end_ns)

    return PacketDrawResult(
        opcode=OPC_E_ASSERT_CE,
        name="E_ASSERT_CE",
        start_ns=start_ns,
        end_ns=end_ns,
        lun=lun,
        ce_signal=ce_signal,
        duration_ns=timing.t_cs_ns,
    )


def draw_e_write_cmd(
    timeline: Timeline,
    *,
    start_ns: float,
    nand_cmd: int,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Append pin activity for NPHY ``E_WRITE_CMD`` (EXTEND opcode 1 / 0x11).

    Per §3: issues one NAND command byte on DQ with CLE high / ALE low and a
    WE# pulse. ``nand_cmd`` is the only per-call field; pass it from the log.

    WEB low width follows CFG ``SDR_WE_LOW_CYCLE`` ↔ ``tWP`` when
    ``EXT_TCALS_CYCLE=0``.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    cmd = nand_cmd & 0xFF
    end_ns = _write_latch_cycle(
        timeline,
        start_ns=start_ns,
        cle=1,
        ale=0,
        data_byte=cmd,
        data_label=f"CMD {cmd:02X}h",
        timing=timing,
    )
    return PacketDrawResult(
        opcode=OPC_E_WRITE_CMD,
        name="E_WRITE_CMD",
        start_ns=start_ns,
        end_ns=end_ns,
        nand_cmd=cmd,
        duration_ns=end_ns - start_ns,
    )


def draw_e_write_addr(
    timeline: Timeline,
    *,
    start_ns: float,
    nand_addr: int,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Append pin activity for NPHY ``E_WRITE_ADDR`` (EXTEND opcode 2 / 0x12).

    Per §3: issues one address byte on DQ with ALE high / CLE low and a WE#
    pulse. ``nand_addr`` is the only per-call field; pass each byte from the log
    (read/program typically 5 bytes, erase 3).
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    addr = nand_addr & 0xFF
    end_ns = _write_latch_cycle(
        timeline,
        start_ns=start_ns,
        cle=0,
        ale=1,
        data_byte=addr,
        data_label=f"ADDR {addr:02X}h",
        timing=timing,
    )
    return PacketDrawResult(
        opcode=OPC_E_WRITE_ADDR,
        name="E_WRITE_ADDR",
        start_ns=start_ns,
        end_ns=end_ns,
        nand_addr=addr,
        duration_ns=end_ns - start_ns,
    )


def draw_e_rpio_compare_repeat(
    timeline: Timeline,
    *,
    start_ns: float,
    repeat_count: int | None = None,
    status_values: Sequence[int] | None = None,
    compare_value: int = 0xC0,
    dq_mask: int = 0xC0,
    repeat_limit: int = 200 * 256,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Draw ``E_RPIO_COMPARE_REPEAT`` status reads until ready.

    The packet fields are fixed in firmware, but the number of hardware
    attempts is only known at execution time. Callers may therefore provide
    either ``repeat_count`` (unknown status bytes are shown as ``XX``) or
    ``status_values``. With status values, drawing stops at the first byte
    satisfying ``(status & dq_mask) == (compare_value & dq_mask)``.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    if repeat_limit <= 0:
        raise ValueError("repeat_limit must be > 0")
    if repeat_count is None:
        requested = len(status_values) if status_values is not None else 1
    else:
        if repeat_count <= 0:
            raise ValueError("repeat_count must be > 0")
        requested = int(repeat_count)
    attempts = min(requested, repeat_limit)
    if status_values is not None and len(status_values) < attempts:
        raise ValueError("status_values must contain at least repeat_count entries")

    expected = compare_value & 0xFF
    mask = dq_mask & 0xFF
    t = start_ns
    matched: bool | None = None
    completed_attempts = 0
    for index in range(attempts):
        status = None if status_values is None else int(status_values[index]) & 0xFF
        t = _append_read_transfer(
            timeline,
            start_ns=t,
            byte_count=1,
            data=None if status is None else (status,),
            label=f"STATUS #{index + 1}",
            timing=timing,
        )
        completed_attempts += 1
        if status is not None and (status & mask) == (expected & mask):
            matched = True
            break

    if status_values is not None and matched is None:
        matched = False
    return PacketDrawResult(
        opcode=OPC_E_RPIO_COMPARE_REPEAT,
        name="E_RPIO_COMPARE_REPEAT",
        start_ns=start_ns,
        end_ns=t,
        duration_ns=t - start_ns,
        cpl=0,
        compare_value=expected,
        dq_mask=mask,
        repeat_count=completed_attempts,
        matched=matched,
    )


def draw_e_read_data_dma(
    timeline: Timeline,
    *,
    start_ns: float,
    byte_count: int,
    free_pause_valid: bool = False,
    drop: bool = False,
    data: Sequence[int] | None = None,
    pause_ns: float = 0.0,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Draw ``E_READ_DATA_DMA`` (``E_RDMA``) Toggle-DDR data output.

    ``byte_count`` and ``free_pause_valid`` are the per-call packet fields.
    ``data`` can be supplied later by a log decoder; otherwise DATA is shown
    as unknown (``XX``). ``pause_ns`` models a PAGE_READY stall when
    ``free_pause_valid`` is set.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    count = int(byte_count)
    if not 1 <= count <= 0x7FFF:
        raise ValueError("byte_count must be 1..32767")
    if pause_ns < 0:
        raise ValueError("pause_ns must be >= 0")
    if pause_ns and not free_pause_valid:
        raise ValueError("pause_ns requires free_pause_valid=True")

    transfer_start_ns = start_ns + float(pause_ns)
    end_ns = _append_read_transfer(
        timeline,
        start_ns=transfer_start_ns,
        byte_count=count,
        data=data,
        label=f"RDMA {count}B" + (" DROP" if drop else ""),
        timing=timing,
    )
    return PacketDrawResult(
        opcode=OPC_E_READ_DATA_DMA,
        name="E_READ_DATA_DMA",
        start_ns=start_ns,
        end_ns=end_ns,
        duration_ns=end_ns - start_ns,
        cpl=0,
        byte_count=count,
        free_pause_valid=bool(free_pause_valid),
        drop=bool(drop),
    )


def draw_b_nop(
    timeline: Timeline,
    *,
    start_ns: float,
    cycles: int | None = None,
    duration_ns: float | None = None,
    cpl: int = 0,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Append timing for NPHY ``B_NOP`` (BASIC opcode 2 / 0x02).

    Per §3: holds DFI levels and waits. No pin toggles — only advances time.

    Parameters
    ----------
    cycles:
        ``CYCLE_NUM`` / wait cycle count from the log (1 cycle ≈ 2.86 ns @ 350 MHz).
        Ignored when ``duration_ns`` is provided.
    duration_ns:
        Explicit wait in nanoseconds. Use this when the caller has already
        converted timing (e.g. tWB, tWHR, tRHW) to ns.
    cpl:
        0 = pure wait (default); 1 = flush (still no pin change; duration applies).
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    if duration_ns is not None:
        if duration_ns < 0:
            raise ValueError("duration_ns must be >= 0")
        wait_ns = float(duration_ns)
        cycle_count = cycles
    else:
        if cycles is None:
            raise ValueError("draw_b_nop requires cycles= or duration_ns=")
        if cycles < 0:
            raise ValueError("cycles must be >= 0")
        cycle_count = int(cycles)
        wait_ns = timing.nop_duration_ns(cycle_count)

    end_ns = start_ns + wait_ns
    timeline.t_max_ns = max(timeline.t_max_ns, end_ns)

    return PacketDrawResult(
        opcode=OPC_B_NOP,
        name="B_NOP",
        start_ns=start_ns,
        end_ns=end_ns,
        cycles=cycle_count,
        duration_ns=wait_ns,
        cpl=cpl & 1,
    )


def draw_e_timer_ctrl(
    timeline: Timeline,
    *,
    start_ns: float,
    ticks: int | None = None,
    duration_ns: float | None = None,
    nphy_op: int = NPHY_OP_TIMER_NOP,
    timer_op: int = TIMER_OP_START,
    timer_id: int = 0,
    cpl: int = 1,
    deassert_ce: bool | None = None,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Append activity for NPHY ``E_TIMER_CTRL`` (EXTEND opcode A / 0x1A).

    Per §3:
      - Advances the timeline by the timer wait (``ticks`` × 0.32 µs, or an
        explicit ``duration_ns`` such as a known tR / tPROG / tERASE).
      - When ``nphy_op`` requests CE deassert (or ``deassert_ce=True``), all
        ``CEx`` lines return high **at the start of this packet** (not at
        timer expiry) — matching PHYUPD/immediate deassert behavior.

    Parameters
    ----------
    ticks:
        ``Timer Ticks`` field from the log. Ignored when ``duration_ns`` is set.
    duration_ns:
        Explicit wait length in ns (useful once tR/tPROG/tERASE are known).
    nphy_op:
        ``NPHY_OP[31:30]``: 0=NOP, 1/2/3=deassert-all-CE variants.
    timer_op:
        ``TIMER_START`` (default) or ``TIMER_EXPIRE`` (modeled as ~0 wait).
    deassert_ce:
        Force CE deassert on/off. Default: ``True`` when ``nphy_op != 0``.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    nphy_op = int(nphy_op) & 0b11
    timer_op = int(timer_op)
    do_deassert = (nphy_op != NPHY_OP_TIMER_NOP) if deassert_ce is None else bool(deassert_ce)

    if do_deassert:
        _deassert_all_ce(timeline, start_ns)

    if timer_op == TIMER_OP_EXPIRE:
        wait_ns = 0.0 if duration_ns is None else max(0.0, float(duration_ns))
        tick_count = ticks
    elif duration_ns is not None:
        if duration_ns < 0:
            raise ValueError("duration_ns must be >= 0")
        wait_ns = float(duration_ns)
        tick_count = ticks
    else:
        if ticks is None:
            raise ValueError("draw_e_timer_ctrl requires ticks= or duration_ns=")
        if ticks < 0:
            raise ValueError("ticks must be >= 0")
        tick_count = int(ticks)
        wait_ns = timing.timer_duration_ns(tick_count)

    end_ns = start_ns + wait_ns
    timeline.t_max_ns = max(timeline.t_max_ns, end_ns)

    return PacketDrawResult(
        opcode=OPC_E_TIMER_CTRL,
        name="E_TIMER_CTRL",
        start_ns=start_ns,
        end_ns=end_ns,
        ticks=tick_count,
        duration_ns=wait_ns,
        nphy_op=nphy_op,
        timer_op=timer_op,
        cpl=cpl & 1,
        deassert_ce=do_deassert,
    )


def draw_e_deassert_all_ce(
    timeline: Timeline,
    *,
    start_ns: float,
    phyupd_chk: int = 0,
    ack_delay_ns: float = 0.0,
) -> PacketDrawResult:
    """Draw ``E_DEASSERT_ALL_CE`` (EXTEND opcode D / 0x1D).

    Runtime firmware uses ``phyupd_chk=0``, so all CE# pins rise immediately.
    ``ack_delay_ns`` is available for a later decoded PHYUPD acknowledgement
    when a nonzero ``phyupd_chk`` mode is encountered.
    """
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))
    check = int(phyupd_chk)
    if not 0 <= check <= 0b11:
        raise ValueError("phyupd_chk must be 0..3")
    if ack_delay_ns < 0:
        raise ValueError("ack_delay_ns must be >= 0")
    if check == 0 and ack_delay_ns:
        raise ValueError("ack_delay_ns requires nonzero phyupd_chk")

    deassert_ns = start_ns + float(ack_delay_ns)
    _deassert_all_ce(timeline, deassert_ns)
    timeline.t_max_ns = max(timeline.t_max_ns, deassert_ns)
    return PacketDrawResult(
        opcode=OPC_E_DEASSERT_ALL_CE,
        name="E_DEASSERT_ALL_CE",
        start_ns=start_ns,
        end_ns=deassert_ns,
        duration_ns=deassert_ns - start_ns,
        cpl=0,
        deassert_ce=True,
        phyupd_chk=check,
    )


# Default §5.1 single-plane column/row address bytes (placeholder for demos).
DEFAULT_READ_ADDR_BYTES: tuple[int, ...] = (0x00, 0x00, 0x01, 0x00, 0x00)


def draw_read_cmd_issue_through_tr(
    timeline: Timeline,
    *,
    start_ns: float = 10.0,
    lun: int = 0,
    addr_bytes: tuple[int, ...] | list[int] = DEFAULT_READ_ADDR_BYTES,
    t_wb_ns: float | None = None,
    t_r_ns: float | None = None,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Synthesize §5.1 Read Cmd Issue through the tR wait after ``E_TIMER_CTRL``.

    Sequence (``ncs_read_send_read_cmd`` / ``__send_read_cmd``):
      E_ASSERT_CE → CMD 00h → ADDR×5 → CMD 30h → B_NOP(tWB) →
      E_TIMER_CTRL(tR, deassert CE)

    NAND R/B# (active-low) falls after CMD 30h and stays busy through the
    tR wait, then returns ready at timer expiry.

    Stops at tR expiry (before the Data Out phase). Returns the final
    ``E_TIMER_CTRL`` draw result; ``timeline.t_max_ns`` covers the full span.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    if len(addr_bytes) != 5:
        raise ValueError("addr_bytes must have 5 entries (col×2 + row×3)")

    wb_ns = timing.t_wb_ns if t_wb_ns is None else float(t_wb_ns)
    tr_ns = timing.t_r_ns if t_r_ns is None else float(t_r_ns)

    t = start_ns
    t = draw_e_assert_ce(timeline, start_ns=t, lun=lun, timing=timing).end_ns
    t = draw_e_write_cmd(timeline, start_ns=t, nand_cmd=0x00, timing=timing).end_ns
    for addr in addr_bytes:
        t = draw_e_write_addr(
            timeline, start_ns=t, nand_addr=addr, timing=timing
        ).end_ns
    t = draw_e_write_cmd(timeline, start_ns=t, nand_cmd=0x30, timing=timing).end_ns
    # After 30h, NAND asserts R/B# busy within tWB and holds it through tR.
    _set_rb_busy(timeline, t, lun, busy=True)
    t = draw_b_nop(timeline, start_ns=t, duration_ns=wb_ns, timing=timing).end_ns
    timer = draw_e_timer_ctrl(
        timeline,
        start_ns=t,
        duration_ns=tr_ns,
        nphy_op=NPHY_OP_DEASSERT_CE_WDMA,
        timing=timing,
    )
    _set_rb_busy(timeline, timer.end_ns, lun, busy=False)
    return timer


def draw_read_sequence(
    timeline: Timeline,
    *,
    start_ns: float = 10.0,
    lun: int = 0,
    addr_bytes: tuple[int, ...] | list[int] = DEFAULT_READ_ADDR_BYTES,
    status_cmd: int = 0x70,
    status_values: Sequence[int] | None = (0xC0,),
    byte_count: int = 16,
    data: Sequence[int] | None = None,
    free_pause_valid: bool = False,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Synthesize the complete single-plane read packet flow in §5.1.

    The reference does not specify the concrete status-command byte, address,
    transfer length, or returned data for a particular request. Defaults use
    conventional status command 70h, the demo address, one ready status, and a
    compact 16-byte unknown RDMA. Callers can replace all execution-dependent
    values when decoded from a real log.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)

    # [Read Cmd Issue] through tR expiry.
    timer_result = draw_read_cmd_issue_through_tr(
        timeline,
        start_ns=start_ns,
        lun=lun,
        addr_bytes=addr_bytes,
        t_wb_ns=timing.read_confirm_nop_ns,
        t_r_ns=timing.t_r_ns,
        timing=timing,
    )
    t = timer_result.end_ns

    # [Data Out] readiness gate.
    t = draw_e_assert_ce(timeline, start_ns=t, lun=lun, timing=timing).end_ns
    t = draw_e_write_cmd(
        timeline, start_ns=t, nand_cmd=status_cmd, timing=timing
    ).end_ns
    t = draw_b_nop(
        timeline, start_ns=t, duration_ns=timing.t_whr_ns, timing=timing
    ).end_ns
    t = draw_e_rpio_compare_repeat(
        timeline,
        start_ns=t,
        status_values=status_values,
        timing=timing,
    ).end_ns
    t = draw_b_nop(
        timeline, start_ns=t, duration_ns=timing.t_rhw_ns, timing=timing
    ).end_ns

    # Random data-output command and Toggle-DDR DMA.
    t = draw_e_write_cmd(
        timeline, start_ns=t, nand_cmd=0x05, timing=timing
    ).end_ns
    for addr in addr_bytes:
        t = draw_e_write_addr(
            timeline, start_ns=t, nand_addr=addr, timing=timing
        ).end_ns
    t = draw_e_write_cmd(
        timeline, start_ns=t, nand_cmd=0xE0, timing=timing
    ).end_ns
    t = draw_b_nop(
        timeline, start_ns=t, duration_ns=timing.t_whr2_ns, timing=timing
    ).end_ns
    t = draw_e_read_data_dma(
        timeline,
        start_ns=t,
        byte_count=byte_count,
        free_pause_valid=free_pause_valid,
        data=data,
        timing=timing,
    ).end_ns
    t = draw_b_nop(
        timeline, start_ns=t, duration_ns=timing.dout_nop_ns, timing=timing
    ).end_ns
    return draw_e_deassert_all_ce(timeline, start_ns=t)
