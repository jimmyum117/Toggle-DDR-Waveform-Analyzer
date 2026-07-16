"""Toggle DDR / NPHY timing constants used when synthesizing waveforms.

Values are the **FW-applied** BiCS8 numbers from
`2_nphy_packet_study_eng.md` §4.1 (databook timing ↔ FW settings mapping).
Exact cycle math (`NPHY_CYCLES`, CE_WAIT_CYCLE register) can be layered on later.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NphyTiming:
    """Waveform-relevant timings in nanoseconds."""

    # CE setup after E_ASSERT_CE (CFG_CTRL2.CE_WAIT_CYCLE ↔ tCS).
    # FW applied (non-ODT / perf-tuned): 10 ns. Databook Table 46 often lists 20 ns.
    t_cs_ns: float = 10.0

    # CLE/ALE / WE SDR timings used by E_WRITE_CMD / E_WRITE_ADDR.
    # WEB low follows SDR_WE_LOW_CYCLE ↔ tWP when EXT_TCALS_CYCLE=0 (§3).
    t_cals_ns: float = 15.0  # CSV CLE/ALE setup (non-ODT); WEB low often uses tWP instead
    t_wp_ns: float = 5.0  # WE# low pulse (FW applied)
    t_wh_ns: float = 5.0  # WE# high pulse (FW applied)
    t_cas_ns: float = 3.0  # Cmd/addr setup before WE falling (estimated, §3 figure)
    t_cah_ns: float = 3.0  # Cmd/addr hold after WE rising (estimated)
    t_calh_ns: float = 3.0  # CLE/ALE hold after cycle (estimated)

    # B_NOP: 1 NPHY cycle ≈ 1/350 MHz (§3 B_NOP).
    nphy_clk_mhz: float = 350.0

    # E_TIMER_CTRL: 1 tick = 256 ctrl cycles = 0.32 µs @ 800 MHz (§3).
    timer_tick_ns: float = 320.0

    # Runtime waits (§4.1 FW-applied / §5.1 read path).
    t_wb_ns: float = 60.0  # WE high → busy; used for read_confirm B_NOP
    t_r_ns: float = 24100.0  # cell→register (tREAD_* low end, 24.1 µs)
    read_confirm_nop_ns: float = 14.0  # derived tWB-adjusted wait after 30h
    t_whr_ns: float = 100.0  # WE# high → RE# low before status read
    t_rhw_ns: float = 40.0  # RE# high → WE# low turnaround
    t_whr2_ns: float = 225.0  # random data-output setup after E0h
    dout_nop_ns: float = 0.0  # derived post-RDMA wait; clamped at zero

    # Read-data waveform generated from NPHY/PHY configuration (§3 E_RDMA).
    t_cr_ns: float = 10.0  # CE asserted → first RE# low
    t_rpre_ns: float = 25.0  # ODT read preamble (tRPRE2)
    t_dqsre_ns: float = 25.0  # RE# → first DQS/data
    t_rc_ns: float = 0.833  # DDR read cycle at 2400 MT/s
    t_rp_ns: float = 0.375  # RE# low width
    t_reh_ns: float = 0.375  # RE# high width
    t_rpst_ns: float = 1.0  # non-ODT postamble approximation
    t_rpsth_ns: float = 25.0  # read postamble hold

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
