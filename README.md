# Toggle DDR Waveform Analyzer

Desktop UI for inspecting Toggle DDR pin waveforms generated from SSD log files.

This repository currently implements the **UI shell only**. Waveform drawing, pin timing, and log parsing are stubbed and can be added later.

## Features (UI)

- Dark logic-analyzer style layout
- Left **Signals** list (CE / CLE / ALE / WEN / REN / DQS / RB / DATA)
- Center **tabbed waveform viewport** — one tab per opened log
- Right **List View / Search View** event panel (placeholder)
- Toolbar: **Open**, **New Tab (Idle)** (temporary), **Save Image**, **Zoom In/Out**, **Fit**
- Closable tabs for each opened log
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
  document.py           # WaveformDocument + ViewState (no timeline yet)
ui/
  main_window.py        # menus, toolbar, splitters, tabs
  waveform_page.py      # one tab
  waveform_view.py      # canvas placeholder + PNG export
  signal_list.py
  event_panel.py
```

## Not implemented yet

- Log file parser (format TBD)
- Toggle DDR timing / edge generation
- Actual digital + DATA hex waveform rendering
- Event list population and search

Open still creates a tab per file so multi-log workflow and Save Image can be exercised without a parser.
