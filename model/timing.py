"""Toggle DDR / NPHY timing constants used when synthesizing waveforms.

Values are the **FW-applied** BiCS8 numbers from
`2_nphy_packet_study_kor.md` §4.1 (databook timing ↔ FW settings mapping).
Exact cycle math (`NPHY_CYCLES`, CE_WAIT_CYCLE register) can be layered on later.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NphyTiming:
    """Waveform-relevant timings in nanoseconds."""

    # CE setup after E_ASSERT_CE (CFG_CTRL2.CE_WAIT_CYCLE ↔ tCS).
    # FW applied (non-ODT / perf-tuned): 10 ns. Databook Table 46 lists 20/30 ns.
    t_cs_ns: float = 10.0

    # CLE/ALE / WE SDR timings used by E_WRITE_CMD / E_WRITE_ADDR.
    # Independent of RE path (tWHR chains from WE# rising, not CLE falling).
    # CLE/ALE (+ DQ) high = tCALS + tCALH.
    # WE# falls tWP before end of tCALS, rises at the tCALS/tCALH boundary.
    # WEB low width ↔ tWP / SDR_WE_LOW_CYCLE when EXT_TCALS_CYCLE=0 (§3).
    t_cals_ns: float = 5.0  # CLE/ALE setup window (FW =tWP)
    t_wp_ns: float = 5.0  # WE# low pulse (FW applied)
    t_wh_ns: float = 5.0  # WE# high pulse (FW applied)
    t_cas_ns: float = 5.0  # estimated (=tWP)
    t_cah_ns: float = 5.0  # estimated (=tCAS)
    t_calh_ns: float = 5.0  # CLE/ALE hold after tCALS (FW estimated)

    # B_NOP: 1 NPHY cycle ≈ 1/350 MHz (§3 B_NOP).
    nphy_clk_mhz: float = 350.0

    # E_TIMER_CTRL: 1 tick = 256 ctrl cycles = 0.32 µs @ 800 MHz (§3).
    timer_tick_ns: float = 320.0

    # Runtime waits (§4.1 FW-applied / §5.1 read path).
    t_wb_ns: float = 60.0  # WE high → busy; used for read_confirm B_NOP
    t_wc_ns: float = 10.0  # write cycle time (used in derived nop formulas)
    t_ceh_ns: float = 30.0  # ONFI CE high between deassert/reassert
    t_r_ns: float = 24100.0  # cell→register (tREAD_* low end, 24.1 µs)
    t_erase_ns: float = 5_000_000.0  # typical block erase time, 5 ms
    t_prog_ns: float = 1_200_000.0  # average program time (Table 48 low end, 1.2 ms)
    t_adl_ns: float = 300.0  # address → data (setup_ddr B_NOP)
    # read_confirm_nop = (tWB − tCS − (tWC − tWP) − tCEH) − 1 → 14 ns
    read_confirm_nop_ns: float = 14.0
    # program confirm B_NOP after 10h/1Ah (§5.3): tWB − tCEH → 30 ns
    program_confirm_nop_ns: float = 30.0
    t_whr_ns: float = 100.0  # WE# high → RE# low before status read
    t_rhw_ns: float = 40.0  # RE# high → WE# low turnaround
    t_whr2_ns: float = 225.0  # random data-output setup after E0h
    # dout_nop = (tRHW − (tCS − tCALS) − tRPSTH − tCEH) − 1 → clamped at 0
    dout_nop_ns: float = 0.0

    # Read-data waveform generated from NPHY/PHY configuration (§3 E_RDMA).
    # Databook tCR; §4.1 marks unused. E_READ_DATA_DMA forces 0 when drawing.
    t_cr_ns: float = 10.0
    t_rpre_ns: float = 25.0  # ODT read preamble (tRPRE2)
    t_dqsre_ns: float = 25.0  # RE# → first DQS/data (FW own value)
    t_rc_ns: float = 0.833  # DDR read cycle at 2400 MT/s
    # Toggle-DDR RE pulse widths (0.45×tRC @2400 MT/s). CFG SDR_RE_LOW_CYCLE=12 ns is SDR-only.
    t_rp_ns: float = 0.375
    t_reh_ns: float = 0.375
    t_rpst_ns: float = 1.0  # non-ODT postamble approximation (table: calculated)
    t_rpsth_ns: float = 25.0  # read postamble hold

    # Write-data waveform (§3 E_WPIO / E_WDMA).
    t_wpre_ns: float = 25.0  # ODT write preamble (tWPRE2)
    t_wpst_ns: float = 7.0  # write postamble (tWPST, non-ODT)
    t_wpsth_ns: float = 25.0  # write postamble hold
    t_dsc_ns: float = 0.833  # DDR write DQS cycle at 2400 MT/s

    # Training / misc packet durations.
    rxrst_ns: float = 11.44  # ≈4 NPHY cycles for B_RXRST
    onfi_reg_rw_ns: float = 5.72  # ≈2 NPHY cycles for B_ONFI_REG_RW
    change_pio_hold_ns: float = 11.44  # B_CHANGE_PIO level hold

    def nphy_cycle_ns(self) -> float:
        return 1000.0 / self.nphy_clk_mhz

    def nop_duration_ns(self, cycles: int) -> float:
        """Duration for a B_NOP wait of ``cycles`` NPHY clocks."""
        return max(0, cycles) * self.nphy_cycle_ns()

    def timer_duration_ns(self, ticks: int) -> float:
        """Duration represented by ``ticks`` timer ticks."""
        return max(0, ticks) * self.timer_tick_ns


# Default timing profile for synthesized waveforms.
DEFAULT_TIMING = NphyTiming()
