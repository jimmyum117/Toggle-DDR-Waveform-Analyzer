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

from dataclasses import dataclass, replace
from typing import Sequence

from model.document import DIGITAL_SIGNALS, INACTIVE_LEVELS
from model.timeline import BusSegment, Edge, Timeline
from model.timing import DEFAULT_TIMING, NphyTiming

# Runtime opcodes as used in lhotse macros (BASIC 0x00–0x0F, EXTEND 0x10–0x1F).
OPC_B_RXRST = 0x00  # BASIC 0
OPC_B_NOP = 0x02  # BASIC 2
OPC_B_CHANGE_PIO = 0x04  # BASIC 4
OPC_B_TEST_WPIO = 0x05  # BASIC 5
OPC_B_TEST_RPIO = 0x07  # BASIC 7
OPC_B_SEND_DUMMY_RDATA = 0x0D  # BASIC D
OPC_B_ONFI_REG_RW = 0x0F  # BASIC F
OPC_E_ASSERT_CE = 0x10  # EXTEND 0
OPC_E_WRITE_CMD = 0x11  # EXTEND 1
OPC_E_WRITE_ADDR = 0x12  # EXTEND 2
OPC_E_WRITE_DATA_PIO = 0x13  # EXTEND 3
OPC_E_WRITE_DATA_DMA = 0x14  # EXTEND 4
OPC_E_WRITE_DATA_RANDOM = 0x15  # EXTEND 5
OPC_E_READ_DATA_PIO = 0x16  # EXTEND 6
OPC_E_READ_DATA_DMA = 0x17  # EXTEND 7
OPC_E_RPIO_COMPARE = 0x18  # EXTEND 8
OPC_E_RPIO_COMPARE_REPEAT = 0x19  # EXTEND 9
OPC_E_TIMER_CTRL = 0x1A  # EXTEND A
OPC_E_DEASSERT_ALL_CE = 0x1D  # EXTEND D

# Named B_CHANGE_PIO presets from §3 training helpers.
CHANGE_PIO_PREAMBLE = "preamble"
CHANGE_PIO_POSTAMBLE = "postamble"
CHANGE_PIO_POSTAMBLE_HOLD = "postamble_hold"
CHANGE_PIO_RPIO = "rpio"

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
    # WE# rising edge for latch packets; use for tWHR/tWB (not end_ns / CLE fall).
    we_rise_ns: float | None = None
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
    group: int | None = None
    data_byte: int | None = None
    mode: str | None = None
    reg_addr: int | None = None
    reg_wdata: int | None = None
    rnw: int | None = None
    timer_id: int | None = None
    status_byte: int | None = None


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
    re_cycle_ns = timing.t_rp_ns + timing.t_reh_ns

    # REN/REP are a true differential pair (always complements).
    # RE pulse widths use Toggle-DDR tRP / tREH (0.45×tRC).
    for cycle in range(cycle_count):
        cycle_ns = first_re_ns + cycle * re_cycle_ns
        _append_edge(timeline, cycle_ns, "REN", 0)
        _append_edge(timeline, cycle_ns, "REP", 1)
        _append_edge(timeline, cycle_ns + timing.t_rp_ns, "REN", 1)
        _append_edge(timeline, cycle_ns + timing.t_rp_ns, "REP", 0)

    # DQS stays in phase with the write/read clock (edges on the tDSC or tRC
    # half-period grid from data_start). DQ is shifted 90° (half_beat = t/4 of
    # a full DQS cycle) so each DQS edge sits at the center of its DQ beat.
    half_beat_ns = beat_ns / 2.0
    for index in range(byte_count):
        dqs_edge_ns = data_start_ns + index * beat_ns
        dqs_p = index & 1
        _append_edge(timeline, dqs_edge_ns, "DQSP", dqs_p)
        _append_edge(timeline, dqs_edge_ns, "DQSN", 1 - dqs_p)
        value = "XX" if data is None else f"{int(data[index]) & 0xFF:02X}"
        _append_bus_value(
            timeline,
            dqs_edge_ns - half_beat_ns,
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


def _append_re_pair(timeline: Timeline, time_ns: float, ren_low: bool) -> None:
    """Drive REN/REP as a differential pair (``ren_low`` → REN=0, REP=1)."""
    if ren_low:
        _append_edge(timeline, time_ns, "REN", 0)
        _append_edge(timeline, time_ns, "REP", 1)
    else:
        _append_edge(timeline, time_ns, "REN", 1)
        _append_edge(timeline, time_ns, "REP", 0)


def _append_status_compare_transfer(
    timeline: Timeline,
    *,
    start_ns: float,
    byte_count: int,
    data: Sequence[int] | None,
    label: str,
    timing: NphyTiming,
) -> float:
    """Draw one ``E_RPIO_COMPARE_REPEAT`` status-out attempt.

    After ``tWHR`` (``start_ns``):
      - REN/REP: ``tRPRE`` preamble, ``tREH`` hold, then one ``tRP``/``tREH``
        cycle per status byte, then ``tRPSTH`` postamble
      - DQS: starts ``tDQSRE`` after ``start_ns``; half-periods reuse ``tRP``
        (low) / ``tREH`` (high) to match REN pulse widths
      - DATA: begins on the second DQS edge; each byte lasts one DQS half-period
    """
    if byte_count <= 0:
        raise ValueError("byte_count must be > 0")
    if data is not None and len(data) < byte_count:
        raise ValueError("data must contain at least byte_count entries")

    t_rpre = timing.t_rpre_ns
    t_dqsre = timing.t_dqsre_ns
    t_rp = timing.t_rp_ns
    t_reh = timing.t_reh_ns
    t_rpsth = timing.t_rpsth_ns
    re_cycle_ns = t_rp + t_reh

    # DQS train: first edge at start+tDQSRE; DATA from the second edge.
    t_dqs = start_ns + t_dqsre

    def _dqs_edge_ns(edge: int) -> float:
        """Edge times with tRP (even→odd) / tREH (odd→even) half-periods."""
        full, rem = divmod(edge, 2)
        return t_dqs + full * re_cycle_ns + (t_rp if rem else 0.0)

    data_start_ns = _dqs_edge_ns(1)
    data_end_ns = _dqs_edge_ns(1 + byte_count)

    # --- DQS + DATA -------------------------------------------------------
    # Edge 0: first toggle (idle High-Z → 0). Edge 1: second toggle → DATA.
    dqs_edge_count = byte_count + 1  # first toggle + one edge per data byte
    for edge in range(dqs_edge_count):
        edge_ns = _dqs_edge_ns(edge)
        dqs_p = edge & 1  # 0, 1, 0, … (visible first edge from High-Z)
        _append_edge(timeline, edge_ns, "DQSP", dqs_p)
        _append_edge(timeline, edge_ns, "DQSN", 1 - dqs_p)

    for index in range(byte_count):
        beat_start_ns = _dqs_edge_ns(1 + index)
        # Half-period after that edge: odd edge → tREH, even edge → tRP.
        beat_ns = t_reh if ((1 + index) & 1) == 1 else t_rp
        value = "XX" if data is None else f"{int(data[index]) & 0xFF:02X}"
        _append_bus_value(
            timeline,
            beat_start_ns,
            beat_ns,
            value,
            label=label if index == 0 else None,
        )

    _append_edge(timeline, data_end_ns, "DQSP", INACTIVE_LEVELS["DQSP"])
    _append_edge(timeline, data_end_ns, "DQSN", INACTIVE_LEVELS["DQSN"])

    # --- REN/REP ----------------------------------------------------------
    # 1) Preamble: low for exactly tRPRE after tWHR, then return high.
    preamble_end_ns = start_ns + t_rpre
    _append_re_pair(timeline, start_ns, ren_low=True)
    _append_re_pair(timeline, preamble_end_ns, ren_low=False)

    # 2) One full tRP (low) + tREH (high) cycle per status byte.
    #    Hold high for tREH after the preamble rise so the first data fall does
    #    not collapse the preamble into a zero-width spike or merge with tRP.
    t = preamble_end_ns + t_reh
    for _ in range(byte_count):
        _append_re_pair(timeline, t, ren_low=True)
        t += t_rp
        _append_re_pair(timeline, t, ren_low=False)
        t += t_reh

    # 3) Postamble tRPSTH begins right after the final data toggle.
    postamble_start_ns = t
    _append_re_pair(timeline, postamble_start_ns, ren_low=True)
    end_ns = postamble_start_ns + t_rpsth
    _append_re_pair(timeline, end_ns, ren_low=False)

    timeline.t_max_ns = max(timeline.t_max_ns, end_ns, data_end_ns)
    return end_ns


def _append_write_transfer(
    timeline: Timeline,
    *,
    start_ns: float,
    byte_count: int,
    data: Sequence[int] | None,
    label: str,
    timing: NphyTiming,
) -> float:
    """Draw configured DQS write activity for one Toggle-DDR write transfer.

    At transfer start (after ``tADL`` in the program sequence) DQS leaves
    High-Z and holds a static driven level through ``tWPRE``. Pulsing begins
    at ``start + tWPRE``.
    """
    if byte_count <= 0:
        raise ValueError("byte_count must be > 0")
    if data is not None and len(data) < byte_count:
        raise ValueError("data must contain at least byte_count entries")

    data_start_ns = start_ns + timing.t_wpre_ns
    beat_ns = timing.t_dsc_ns / 2.0
    # WE# stays high during DDR data-in; DQS carries the clock.
    _append_edge(timeline, start_ns, "WEN", 1)
    _append_edge(timeline, start_ns, "CLE", 0)
    _append_edge(timeline, start_ns, "ALE", 0)
    # Keep REN/REP differential while write data-in holds RE inactive.
    _append_edge(timeline, start_ns, "REN", 1)
    _append_edge(timeline, start_ns, "REP", 0)

    # Leave High-Z at transfer start; hold static (no pulse) through tWPRE.
    # Drive high so the first data beat (DQSP=0) is a visible falling edge.
    _append_edge(timeline, start_ns, "DQSP", 1)
    _append_edge(timeline, start_ns, "DQSN", 0)

    # DQS stays in phase with the write clock (edges on the tDSC/2 grid from
    # data_start). DQ is shifted 90° earlier (half_beat = tDSC/4) so each DQS
    # edge sits at the center of its DQ beat.
    half_beat_ns = beat_ns / 2.0
    for index in range(byte_count):
        dqs_edge_ns = data_start_ns + index * beat_ns
        dqs_p = index & 1
        _append_edge(timeline, dqs_edge_ns, "DQSP", dqs_p)
        _append_edge(timeline, dqs_edge_ns, "DQSN", 1 - dqs_p)
        value = "XX" if data is None else f"{int(data[index]) & 0xFF:02X}"
        _append_bus_value(
            timeline,
            dqs_edge_ns - half_beat_ns,
            beat_ns,
            value,
            label=label if index == 0 else None,
        )

    last_dqs_pulse_ns = data_start_ns + (byte_count - 1) * beat_ns
    end_ns = last_dqs_pulse_ns + timing.t_wpst_ns + timing.t_wpsth_ns
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
) -> tuple[float, float]:
    """Common CLE/ALE + WE# pulse for command/address latch cycles.

    CLE/ALE (+ DQ) and WE# are timed independently:
      - CLE/ALE high duration = ``tCALS + tCALH``
      - WE# falls ``tWP`` before the end of the ``tCALS`` window and stays
        low for ``tWP`` (rises at the tCALS / tCALH boundary)

    Returns ``(we_rise_ns, cle_end_ns)``. Chain the next latch from
    ``cle_end_ns`` so DQ windows do not overlap; chain ``tWHR`` / ``tWB``
    from ``we_rise_ns``.
    """
    t_cals = timing.t_cals_ns
    t_calh = timing.t_calh_ns
    t_wp = timing.t_wp_ns

    t_cle_assert = start_ns
    t_cals_end = t_cle_assert + t_cals
    # WEN low is anchored to the end of tCALS, not delayed by tCALS itself.
    t_we_fall = t_cals_end - t_wp
    t_we_rise = t_cals_end
    t_cle_end = t_cle_assert + t_cals + t_calh

    _append_edge(timeline, t_cle_assert, "CLE", 1 if cle else 0)
    _append_edge(timeline, t_cle_assert, "ALE", 1 if ale else 0)
    _append_edge(timeline, t_we_fall, "WEN", 0)
    _append_edge(timeline, t_we_rise, "WEN", 1)

    # DQ follows CLE/ALE window (tCALS + tCALH), not the WE# low width.
    _append_data(
        timeline,
        t_cle_assert,
        t_cle_end - t_cle_assert,
        data_byte,
        label=data_label,
    )

    _append_edge(timeline, t_cle_end, "CLE", 0)
    _append_edge(timeline, t_cle_end, "ALE", 0)

    timeline.t_max_ns = max(timeline.t_max_ns, t_cle_end, t_we_rise)
    return t_we_rise, t_cle_end


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
    we_rise_ns, end_ns = _write_latch_cycle(
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
        we_rise_ns=we_rise_ns,
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
    we_rise_ns, end_ns = _write_latch_cycle(
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
        we_rise_ns=we_rise_ns,
        nand_addr=addr,
        duration_ns=end_ns - start_ns,
    )


def draw_e_write_data_pio(
    timeline: Timeline,
    *,
    start_ns: float,
    data_byte: int,
    write_byte1: int | None = None,
    group: int = 0,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Draw ``E_WRITE_DATA_PIO`` (``E_WPIO``, EXTEND 3 / 0x13).

    Per §3: PIO write of ``write_byte0``/``write_byte1`` (FW usually copies the
    same ``data`` into both). Used mainly for Set Feature parameters.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    b0 = int(data_byte) & 0xFF
    b1 = b0 if write_byte1 is None else int(write_byte1) & 0xFF
    end_ns = _append_write_transfer(
        timeline,
        start_ns=start_ns,
        byte_count=2,
        data=(b0, b1),
        label=f"WPIO {b0:02X}h",
        timing=timing,
    )
    return PacketDrawResult(
        opcode=OPC_E_WRITE_DATA_PIO,
        name="E_WRITE_DATA_PIO",
        start_ns=start_ns,
        end_ns=end_ns,
        duration_ns=end_ns - start_ns,
        cpl=0,
        byte_count=2,
        data_byte=b0,
        group=int(group) & 1,
    )


def draw_e_write_data_dma(
    timeline: Timeline,
    *,
    start_ns: float,
    byte_count: int,
    drop: bool = False,
    data: Sequence[int] | None = None,
    cpl: int = 0,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Draw ``E_WRITE_DATA_DMA`` (``E_WDMA``, EXTEND 4 / 0x14) program data-in."""
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    count = int(byte_count)
    if not 1 <= count <= 0x7FFF:
        raise ValueError("byte_count must be 1..32767")

    end_ns = _append_write_transfer(
        timeline,
        start_ns=start_ns,
        byte_count=count,
        data=None if drop else data,
        label=f"WDMA {count}B" + (" DROP" if drop else ""),
        timing=timing,
    )
    return PacketDrawResult(
        opcode=OPC_E_WRITE_DATA_DMA,
        name="E_WRITE_DATA_DMA",
        start_ns=start_ns,
        end_ns=end_ns,
        duration_ns=end_ns - start_ns,
        cpl=cpl & 1,
        byte_count=count,
        drop=bool(drop),
    )


def draw_e_write_data_random(
    timeline: Timeline,
    *,
    start_ns: float,
    byte_count: int,
    data: Sequence[int] | None = None,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Draw ``E_WRITE_DATA_RANDOM`` (``E_WDMA_RAND``, EXTEND 5 / 0x15).

    Fault-injection path: HW-generated pattern. Without ``data``, beats show
    ``XX`` while still consuming write-path timing.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    count = int(byte_count)
    if not 1 <= count <= 0x7FFF:
        raise ValueError("byte_count must be 1..32767")

    end_ns = _append_write_transfer(
        timeline,
        start_ns=start_ns,
        byte_count=count,
        data=data,
        label=f"WDMA_RAND {count}B",
        timing=timing,
    )
    return PacketDrawResult(
        opcode=OPC_E_WRITE_DATA_RANDOM,
        name="E_WRITE_DATA_RANDOM",
        start_ns=start_ns,
        end_ns=end_ns,
        duration_ns=end_ns - start_ns,
        cpl=0,
        byte_count=count,
    )


def draw_e_read_data_pio(
    timeline: Timeline,
    *,
    start_ns: float,
    data_byte: int | None = None,
    group: int = 0,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Draw ``E_READ_DATA_PIO`` (``E_RPIO``, EXTEND 6 / 0x16) 1-byte PIO read."""
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    payload = None if data_byte is None else (int(data_byte) & 0xFF,)
    end_ns = _append_read_transfer(
        timeline,
        start_ns=start_ns,
        byte_count=1,
        data=payload,
        label="RPIO",
        timing=timing,
    )
    return PacketDrawResult(
        opcode=OPC_E_READ_DATA_PIO,
        name="E_READ_DATA_PIO",
        start_ns=start_ns,
        end_ns=end_ns,
        duration_ns=end_ns - start_ns,
        cpl=1,
        byte_count=1,
        data_byte=None if data_byte is None else int(data_byte) & 0xFF,
        group=int(group) & 1,
    )


def draw_e_rpio_compare(
    timeline: Timeline,
    *,
    start_ns: float,
    status_byte: int | None = None,
    compare_value: int = 0xC0,
    dq_mask: int = 0xC0,
    timer_id: int = 0,
    cpl: int = 1,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Draw ``E_RPIO_COMPARE`` (EXTEND 8 / 0x18) single status read + compare.

    Uses the same status-out waveform as ``E_RPIO_COMPARE_REPEAT`` (one
    attempt): ``tRPRE`` REN preamble, ``tREH`` hold, one ``tRP``/``tREH``
    cycle, ``tRPSTH``; DQS from ``tDQSRE`` with ``tRP``/``tREH`` half-periods;
    DATA from the second DQS edge.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    expected = compare_value & 0xFF
    mask = dq_mask & 0xFF
    status = None if status_byte is None else int(status_byte) & 0xFF
    end_ns = _append_status_compare_transfer(
        timeline,
        start_ns=start_ns,
        byte_count=1,
        data=None if status is None else (status,),
        label="STATUS COMP",
        timing=timing,
    )
    matched: bool | None = None
    if status is not None:
        matched = (status & mask) == (expected & mask)
    return PacketDrawResult(
        opcode=OPC_E_RPIO_COMPARE,
        name="E_RPIO_COMPARE",
        start_ns=start_ns,
        end_ns=end_ns,
        duration_ns=end_ns - start_ns,
        cpl=cpl & 1,
        compare_value=expected,
        dq_mask=mask,
        matched=matched,
        timer_id=int(timer_id) & 0x3F,
        status_byte=status,
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

    Waveform (per attempt, ``start_ns`` = after ``tWHR``): REN preamble
    ``tRPRE``, ``tREH`` hold, one ``tRP``/``tREH`` cycle per status byte,
    then ``tRPSTH``; DQS from ``tDQSRE`` with matching ``tRP``/``tREH``
    half-periods; DATA from the second DQS edge.
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
        t = _append_status_compare_transfer(
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

    Per §4.1, ``tCR`` is unused by FW, so the first RE# falls at transfer
    start (after optional ``pause_ns``) rather than ``start + tCR``.
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

    # §4.1: tCR is unused — do not insert CE→RE setup before the RE train.
    rdma_timing = replace(timing, t_cr_ns=0.0)
    transfer_start_ns = start_ns + float(pause_ns)
    end_ns = _append_read_transfer(
        timeline,
        start_ns=transfer_start_ns,
        byte_count=count,
        data=data,
        label=f"RDMA {count}B" + (" DROP" if drop else ""),
        timing=rdma_timing,
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


_CHANGE_PIO_PRESETS: dict[str, dict[str, int]] = {
    CHANGE_PIO_PREAMBLE: {
        "cle": 0,
        "ale": 0,
        "wen": 1,
        "ren": 1,
        "wdqs": 1,
    },
    CHANGE_PIO_POSTAMBLE: {
        "cle": 1,
        "ale": 1,
        "wen": 1,
        "ren": 1,
        "wdqs": 1,
    },
    CHANGE_PIO_POSTAMBLE_HOLD: {
        "cle": 1,
        "ale": 1,
        "wen": 1,
        "ren": 1,
        "wdqs": 0,
    },
    CHANGE_PIO_RPIO: {
        "cle": 0,
        "ale": 0,
        "wen": 1,
        "ren": 0,
        "wdqs": 0,
    },
}


def draw_b_rxrst(
    timeline: Timeline,
    *,
    start_ns: float,
    dq_rxrst: int = 0b01,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Advance time for ``B_RXRST`` (BASIC 0 / 0x00).

    Asserts DFI_RXRST internally; no Toggle Flash pin edges are visible.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    wait_ns = float(timing.rxrst_ns)
    end_ns = start_ns + wait_ns
    timeline.t_max_ns = max(timeline.t_max_ns, end_ns)
    return PacketDrawResult(
        opcode=OPC_B_RXRST,
        name="B_RXRST",
        start_ns=start_ns,
        end_ns=end_ns,
        duration_ns=wait_ns,
        cpl=0,
        mode=f"dq_rxrst={int(dq_rxrst) & 0b11:02b}",
    )


def draw_b_change_pio(
    timeline: Timeline,
    *,
    start_ns: float,
    mode: str | None = CHANGE_PIO_PREAMBLE,
    cle: int | None = None,
    ale: int | None = None,
    wen: int | None = None,
    ren: int | None = None,
    wdqs: int | None = None,
    hold_ns: float | None = None,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Draw ``B_CHANGE_PIO`` (BASIC 4 / 0x04) DFI signal sketch levels.

    Pass ``mode`` as one of the §3 training presets (``preamble``,
    ``postamble``, ``postamble_hold``, ``rpio``), or override individual
    CLE/ALE/WE#/RE#/DQS levels.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    levels = {
        "cle": 0,
        "ale": 0,
        "wen": 1,
        "ren": 1,
        "wdqs": 0,
    }
    if mode is not None:
        if mode not in _CHANGE_PIO_PRESETS:
            raise ValueError(
                f"unknown CHANGE_PIO mode {mode!r}; "
                f"expected one of {sorted(_CHANGE_PIO_PRESETS)}"
            )
        levels.update(_CHANGE_PIO_PRESETS[mode])
    if cle is not None:
        levels["cle"] = int(cle) & 1
    if ale is not None:
        levels["ale"] = int(ale) & 1
    if wen is not None:
        levels["wen"] = int(wen) & 1
    if ren is not None:
        levels["ren"] = int(ren) & 1
    if wdqs is not None:
        levels["wdqs"] = int(wdqs) & 1

    _append_edge(timeline, start_ns, "CLE", levels["cle"])
    _append_edge(timeline, start_ns, "ALE", levels["ale"])
    _append_edge(timeline, start_ns, "WEN", levels["wen"])
    _append_edge(timeline, start_ns, "REN", levels["ren"])
    _append_edge(timeline, start_ns, "REP", 1 - levels["ren"])
    dqs_p = levels["wdqs"]
    _append_edge(timeline, start_ns, "DQSP", dqs_p)
    _append_edge(timeline, start_ns, "DQSN", 1 - dqs_p)

    wait_ns = float(timing.change_pio_hold_ns if hold_ns is None else hold_ns)
    if wait_ns < 0:
        raise ValueError("hold_ns must be >= 0")
    end_ns = start_ns + wait_ns
    timeline.t_max_ns = max(timeline.t_max_ns, end_ns)
    return PacketDrawResult(
        opcode=OPC_B_CHANGE_PIO,
        name="B_CHANGE_PIO",
        start_ns=start_ns,
        end_ns=end_ns,
        duration_ns=wait_ns,
        cpl=0,
        mode=mode,
    )


def draw_b_test_wpio(
    timeline: Timeline,
    *,
    start_ns: float,
    data: Sequence[int] | None = None,
    dfi_cle: int = 0,
    dfi_web: int = 0x0,
    dfi_wdqs_ctrl: int = 0x55,
    keep_cnt: int = 3,
    phase_count: int = 4,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Draw ``B_TEST_WPIO`` (BASIC 5 / 0x05) write-training sketch.

    Approximates multi-phase WE#/DQS/DATA toggles from the 4-DW training
    packet. ``dfi_web`` / ``dfi_wdqs_ctrl`` are packed per-phase bit fields.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    phases = max(1, int(phase_count))
    hold_ns = max(1, int(keep_cnt)) * timing.nphy_cycle_ns()
    payload = None if data is None else [int(b) & 0xFF for b in data[:phases]]

    _append_edge(timeline, start_ns, "CLE", int(dfi_cle) & 1)
    _append_edge(timeline, start_ns, "ALE", 0)
    _append_edge(timeline, start_ns, "REN", 1)
    _append_edge(timeline, start_ns, "REP", 0)

    t = start_ns
    for phase in range(phases):
        wen = (int(dfi_web) >> phase) & 1
        # 2 bits per phase in dfi_wdqs_ctrl[31:24] style packing (LSB-first).
        wdqs = (int(dfi_wdqs_ctrl) >> (2 * phase)) & 0b11
        dqs_p = 1 if wdqs else 0
        _append_edge(timeline, t, "WEN", wen)
        _append_edge(timeline, t, "DQSP", dqs_p)
        _append_edge(timeline, t, "DQSN", 1 - dqs_p)
        if payload is None or phase >= len(payload):
            value = "XX"
        else:
            value = f"{payload[phase]:02X}"
        _append_bus_value(
            timeline,
            t,
            hold_ns,
            value,
            label="TEST_WPIO" if phase == 0 else None,
        )
        t += hold_ns

    _append_edge(timeline, t, "CLE", 0)
    _append_edge(timeline, t, "WEN", INACTIVE_LEVELS["WEN"])
    _append_edge(timeline, t, "DQSP", INACTIVE_LEVELS["DQSP"])
    _append_edge(timeline, t, "DQSN", INACTIVE_LEVELS["DQSN"])
    timeline.t_max_ns = max(timeline.t_max_ns, t)
    return PacketDrawResult(
        opcode=OPC_B_TEST_WPIO,
        name="B_TEST_WPIO",
        start_ns=start_ns,
        end_ns=t,
        duration_ns=t - start_ns,
        cpl=0,
        byte_count=phases,
    )


def draw_b_test_rpio(
    timeline: Timeline,
    *,
    start_ns: float,
    data: Sequence[int] | None = None,
    dfi_re_ctrl: int = 0xF,
    keep_cnt: int = 3,
    phase_count: int = 4,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Draw ``B_TEST_RPIO`` (BASIC 7 / 0x07) read-training capture sketch."""
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    phases = max(1, int(phase_count))
    hold_ns = max(1, int(keep_cnt)) * timing.nphy_cycle_ns()
    payload = None if data is None else [int(b) & 0xFF for b in data[:phases]]

    _append_edge(timeline, start_ns, "CLE", 0)
    _append_edge(timeline, start_ns, "ALE", 0)
    _append_edge(timeline, start_ns, "WEN", 1)

    t = start_ns
    for phase in range(phases):
        re_on = (int(dfi_re_ctrl) >> phase) & 1
        _append_edge(timeline, t, "REN", 0 if re_on else 1)
        _append_edge(timeline, t, "REP", 1 if re_on else 0)
        dqs_p = phase & 1
        _append_edge(timeline, t, "DQSP", dqs_p)
        _append_edge(timeline, t, "DQSN", 1 - dqs_p)
        value = "XX" if payload is None else f"{payload[phase]:02X}"
        _append_bus_value(
            timeline,
            t,
            hold_ns,
            value,
            label="TEST_RPIO" if phase == 0 else None,
        )
        t += hold_ns

    _append_edge(timeline, t, "REN", INACTIVE_LEVELS["REN"])
    _append_edge(timeline, t, "REP", INACTIVE_LEVELS["REP"])
    _append_edge(timeline, t, "DQSP", INACTIVE_LEVELS["DQSP"])
    _append_edge(timeline, t, "DQSN", INACTIVE_LEVELS["DQSN"])
    timeline.t_max_ns = max(timeline.t_max_ns, t)
    return PacketDrawResult(
        opcode=OPC_B_TEST_RPIO,
        name="B_TEST_RPIO",
        start_ns=start_ns,
        end_ns=t,
        duration_ns=t - start_ns,
        cpl=1,
        byte_count=phases,
    )


def draw_b_send_dummy_rdata(
    timeline: Timeline,
    *,
    start_ns: float,
    byte_count: int,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Advance time for ``B_SEND_DUMMY_RDATA`` (BASIC D / 0x0D).

    Pads datapath DMA length without toggling Flash pins.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    count = int(byte_count)
    if count < 0:
        raise ValueError("byte_count must be >= 0")
    # Match RDMA beat rate so MU padding occupies comparable wall time.
    wait_ns = count * (timing.t_rc_ns / 2.0)
    end_ns = start_ns + wait_ns
    timeline.t_max_ns = max(timeline.t_max_ns, end_ns)
    return PacketDrawResult(
        opcode=OPC_B_SEND_DUMMY_RDATA,
        name="B_SEND_DUMMY_RDATA",
        start_ns=start_ns,
        end_ns=end_ns,
        duration_ns=wait_ns,
        cpl=0,
        byte_count=count,
    )


def draw_b_onfi_reg_rw(
    timeline: Timeline,
    *,
    start_ns: float,
    reg_addr: int = 0x22C,
    reg_wdata: int = 0,
    rnw: int = 0,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Advance time for ``B_ONFI_REG_RW`` (BASIC F / 0x0F).

    Region3 register access (typically DYNAMIC_DFI_WARMUP_VLD_EN @ 0x22c).
    No Flash pin activity.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    ensure_idle_baseline(timeline, at_ns=min(timeline.t_min_ns, start_ns))

    wait_ns = float(timing.onfi_reg_rw_ns)
    end_ns = start_ns + wait_ns
    timeline.t_max_ns = max(timeline.t_max_ns, end_ns)
    return PacketDrawResult(
        opcode=OPC_B_ONFI_REG_RW,
        name="B_ONFI_REG_RW",
        start_ns=start_ns,
        end_ns=end_ns,
        duration_ns=wait_ns,
        cpl=0,
        reg_addr=int(reg_addr) & 0xFFF,
        reg_wdata=int(reg_wdata) & 0xFFFFFFFF,
        rnw=int(rnw) & 1,
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


# Default §5.1 / §5.3 single-plane column/row address bytes (placeholder for demos).
DEFAULT_READ_ADDR_BYTES: tuple[int, ...] = (0x00, 0x00, 0x01, 0x00, 0x00)
DEFAULT_PROGRAM_ADDR_BYTES: tuple[int, ...] = DEFAULT_READ_ADDR_BYTES

# Default §5.2 single-plane row address bytes (placeholder for demos).
DEFAULT_ERASE_ADDR_BYTES: tuple[int, ...] = (0x01, 0x00, 0x00)


def draw_program_cmd_issue(
    timeline: Timeline,
    *,
    start_ns: float = 10.0,
    lun: int = 0,
    addr_bytes: tuple[int, ...] | list[int] = DEFAULT_PROGRAM_ADDR_BYTES,
    confirm_cmd: int = 0x10,
    byte_count: int = 16,
    data: Sequence[int] | None = None,
    slc_cmd: int | None = None,
    page_type_cmd: int | None = None,
    t_adl_ns: float | None = None,
    t_confirm_nop_ns: float | None = None,
    t_prog_ns: float | None = None,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Synthesize the single-plane §5.3 Program Cmd Issue (last plane) flow.

    Sequence (``__send_program_cmd`` / ``__start_program_timer`` last plane):
      B_NOP(10 cycles) → E_ASSERT_CE → [optional SLC] → [optional page-type] →
      CMD 80h → ADDR×5 → B_NOP(tADL) → E_WRITE_DATA_DMA → CMD 10h/1Ah →
      B_NOP(tWB−tCEH) → E_TIMER_CTRL(tPROG, deassert CE)

    R/B# falls after the confirm command and stays busy through tPROG.
    Multi-plane dummy-busy (11h) and post-tPROG status polling are outside
    this helper. Defaults omit SLC / page-type prefixes (TLC MSB last-plane
    style with confirm ``10h``).
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    if len(addr_bytes) != 5:
        raise ValueError("addr_bytes must have 5 entries (col×2 + row×3)")

    adl_ns = timing.t_adl_ns if t_adl_ns is None else float(t_adl_ns)
    confirm_nop_ns = (
        timing.program_confirm_nop_ns
        if t_confirm_nop_ns is None
        else float(t_confirm_nop_ns)
    )
    prog_ns = timing.t_prog_ns if t_prog_ns is None else float(t_prog_ns)
    if adl_ns < 0:
        raise ValueError("t_adl_ns must be >= 0")
    if confirm_nop_ns < 0:
        raise ValueError("t_confirm_nop_ns must be >= 0")
    if prog_ns < 0:
        raise ValueError("t_prog_ns must be >= 0")

    t = start_ns
    # lld_nphy_wait_nop_tick(10) before CE assert.
    t = draw_b_nop(timeline, start_ns=t, cycles=10, timing=timing).end_ns
    t = draw_e_assert_ce(timeline, start_ns=t, lun=lun, timing=timing).end_ns
    if slc_cmd is not None:
        t = draw_e_write_cmd(
            timeline, start_ns=t, nand_cmd=int(slc_cmd) & 0xFF, timing=timing
        ).end_ns
    if page_type_cmd is not None:
        t = draw_e_write_cmd(
            timeline,
            start_ns=t,
            nand_cmd=int(page_type_cmd) & 0xFF,
            timing=timing,
        ).end_ns
    t = draw_e_write_cmd(timeline, start_ns=t, nand_cmd=0x80, timing=timing).end_ns
    for addr in addr_bytes:
        t = draw_e_write_addr(
            timeline, start_ns=t, nand_addr=addr, timing=timing
        ).end_ns
    # setup_ddr(tADL): B_NOP between address and data-in.
    t = draw_b_nop(
        timeline, start_ns=t, duration_ns=adl_ns, timing=timing
    ).end_ns
    t = draw_e_write_data_dma(
        timeline,
        start_ns=t,
        byte_count=byte_count,
        data=data,
        timing=timing,
    ).end_ns
    confirm = draw_e_write_cmd(
        timeline, start_ns=t, nand_cmd=int(confirm_cmd) & 0xFF, timing=timing
    )
    we_confirm = (
        confirm.we_rise_ns if confirm.we_rise_ns is not None else confirm.end_ns
    )
    _set_rb_busy(timeline, we_confirm, lun, busy=True)
    t = draw_b_nop(
        timeline, start_ns=we_confirm, duration_ns=confirm_nop_ns, timing=timing
    ).end_ns
    timer = draw_e_timer_ctrl(
        timeline,
        start_ns=t,
        duration_ns=prog_ns,
        nphy_op=NPHY_OP_DEASSERT_CE_WDMA,
        timing=timing,
    )
    _set_rb_busy(timeline, timer.end_ns, lun, busy=False)
    return timer


def draw_erase_cmd_issue(
    timeline: Timeline,
    *,
    start_ns: float = 10.0,
    lun: int = 0,
    addr_bytes: tuple[int, ...] | list[int] = DEFAULT_ERASE_ADDR_BYTES,
    t_wb_ns: float | None = None,
    t_erase_ns: float | None = None,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Synthesize the single-plane ``Erase Cmd Issue`` flow from §5.2.

    Sequence:
      E_ASSERT_CE → CMD 60h → ADDR×3 → CMD D0h → B_NOP(tWB) →
      E_TIMER_CTRL(tERASE, deassert CE)

    R/B# falls after D0h and remains busy through tWB and tERASE, returning
    ready at timer expiry. Status polling after expiry is outside this flow.
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)
    if len(addr_bytes) != 3:
        raise ValueError("addr_bytes must have 3 row-address entries")

    wb_ns = timing.t_wb_ns if t_wb_ns is None else float(t_wb_ns)
    erase_ns = timing.t_erase_ns if t_erase_ns is None else float(t_erase_ns)
    if wb_ns < 0:
        raise ValueError("t_wb_ns must be >= 0")
    if erase_ns < 0:
        raise ValueError("t_erase_ns must be >= 0")

    t = start_ns
    t = draw_e_assert_ce(timeline, start_ns=t, lun=lun, timing=timing).end_ns
    t = draw_e_write_cmd(timeline, start_ns=t, nand_cmd=0x60, timing=timing).end_ns
    for addr in addr_bytes:
        t = draw_e_write_addr(
            timeline, start_ns=t, nand_addr=addr, timing=timing
        ).end_ns
    cmd_d0 = draw_e_write_cmd(timeline, start_ns=t, nand_cmd=0xD0, timing=timing)
    we_d0 = cmd_d0.we_rise_ns if cmd_d0.we_rise_ns is not None else cmd_d0.end_ns
    _set_rb_busy(timeline, we_d0, lun, busy=True)
    t = draw_b_nop(
        timeline, start_ns=we_d0, duration_ns=wb_ns, timing=timing
    ).end_ns
    timer = draw_e_timer_ctrl(
        timeline,
        start_ns=t,
        duration_ns=erase_ns,
        nphy_op=NPHY_OP_DEASSERT_CE_WDMA,
        timing=timing,
    )
    _set_rb_busy(timeline, timer.end_ns, lun, busy=False)
    return timer


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
    cmd30 = draw_e_write_cmd(timeline, start_ns=t, nand_cmd=0x30, timing=timing)
    # tWB / R/B# busy from WE# rising of 30h (not CLE/DQ release).
    we30 = cmd30.we_rise_ns if cmd30.we_rise_ns is not None else cmd30.end_ns
    _set_rb_busy(timeline, we30, lun, busy=True)
    t = draw_b_nop(timeline, start_ns=we30, duration_ns=wb_ns, timing=timing).end_ns
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
    t_wb_ns: float | None = None,
    t_r_ns: float | None = None,
    timing: NphyTiming | None = None,
) -> PacketDrawResult:
    """Synthesize the complete single-plane read packet flow in §5.1.

    The reference does not specify the concrete status-command byte, address,
    transfer length, or returned data for a particular request. Defaults use
    conventional status command 70h, the demo address, one ready status, and a
    compact 16-byte unknown RDMA. Callers can replace all execution-dependent
    values when decoded from a real log.

    ``t_wb_ns`` / ``t_r_ns`` override the Read Cmd Issue waits (defaults:
    ``timing.read_confirm_nop_ns`` and ``timing.t_r_ns``).
    """
    timing = timing or DEFAULT_TIMING
    _require_start_ns(start_ns)

    # [Read Cmd Issue] through tR expiry.
    timer_result = draw_read_cmd_issue_through_tr(
        timeline,
        start_ns=start_ns,
        lun=lun,
        addr_bytes=addr_bytes,
        t_wb_ns=(
            timing.read_confirm_nop_ns if t_wb_ns is None else float(t_wb_ns)
        ),
        t_r_ns=timing.t_r_ns if t_r_ns is None else float(t_r_ns),
        timing=timing,
    )
    t = timer_result.end_ns

    # [Data Out] readiness gate.
    t = draw_e_assert_ce(timeline, start_ns=t, lun=lun, timing=timing).end_ns
    status = draw_e_write_cmd(
        timeline, start_ns=t, nand_cmd=status_cmd, timing=timing
    )
    # tWHR from WE# rising of status CMD (not CLE/DQ release).
    we_status = status.we_rise_ns if status.we_rise_ns is not None else status.end_ns
    t = draw_b_nop(
        timeline, start_ns=we_status, duration_ns=timing.t_whr_ns, timing=timing
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
    cmd_e0 = draw_e_write_cmd(
        timeline, start_ns=t, nand_cmd=0xE0, timing=timing
    )
    # tWHR2 from WE# rising of E0h.
    we_e0 = cmd_e0.we_rise_ns if cmd_e0.we_rise_ns is not None else cmd_e0.end_ns
    t = draw_b_nop(
        timeline, start_ns=we_e0, duration_ns=timing.t_whr2_ns, timing=timing
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
