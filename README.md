# Toggle DDR Waveform Analyzer

Desktop UI for inspecting Toggle DDR pin waveforms generated from SSD log files.

This repository currently implements the **UI shell only**. Waveform drawing, pin timing, and log parsing are stubbed and can be added later.

## Features (UI)

- Dark logic-analyzer style layout
- Left **Signals** list (CE / CLE / ALE / WEN / REN / DQS / RB / WP / DATA)
- Center **tabbed waveform viewport** — one tab per opened log
- Right **List View** populated by markers (`Mark`, `Sample`, `Time(ns)`, `Diff`)
- Markers: right-click the waveform to drop; list row selection highlights the marker; **Delete Marker** removes the selected one
- Toolbar: **Open**, **New Tab (Idle)**, **Save Image**, **Zoom In/Out**, **Fit**, **Clear Markers**
- Idle demo tab draws steady inactive levels (active-low pins high, active-high pins low; DATA = ZZ)
- Save the active waveform viewport as a PNG

## Requirements

- Python 3.10+
- PySide6

```bash
cd "Toggle DDR Waveform Analyzer"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Project layout

```
main.py                 # app entry
model/
  document.py           # WaveformDocument + ViewState
  timeline.py           # edges / bus segments
  markers.py            # marker list helpers
  timing.py             # NPHY timing constants (tCS, …)
decode/
  nphy_packets.py       # packet → timeline drawers (e.g. draw_e_assert_ce)
ui/
  main_window.py        # menus, toolbar, splitters, tabs
  waveform_page.py      # one tab
  waveform_view.py      # canvas + markers + PNG export
  signal_list.py
  event_panel.py
  layout_metrics.py     # shared track heights
```

## Not implemented yet

- Log file parser (format TBD; expected to be a series of NPHY opcodes)
- Full Toggle DDR timing / edge generation for all packets
- Event search over decoded protocol events

When the log parser lands, call helpers such as ``decode.draw_e_assert_ce``,
``draw_e_write_cmd``, ``draw_e_write_addr``, ``draw_b_nop``, and
``draw_e_timer_ctrl`` in opcode order. Read/status helpers include
``draw_e_rpio_compare_repeat`` and ``draw_e_read_data_dma``; sequences finish
with ``draw_e_deassert_all_ce``. The existing viewport renders the resulting
edges. Wait packets accept ``cycles`` / ``ticks`` or an explicit
``duration_ns`` override.
