# Storage Hunter Roblox Auto Bid - Low-Latency Screen Timing & Outline Macro

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-informational.svg)](https://www.microsoft.com/windows)

A fast, low-latency screen-based timing macro controller for Windows. Auto Bid detects on-screen visual cues (such as moving target bars, slider overlap indicators, or highlight outlines) and fires keystrokes with millisecond precision.

---

## Core Versions & Features

This repository includes three tailored controllers to suit different game mechanics:

### 1. `auto_bid_v2_1.py` (Recommended: Ultra-Low Latency)
* **Outline Detection Engine**: Samples thin border bands around the selected panel edge.
* **1ms Windows Multimedia Timer**: Activates `timeBeginPeriod(1)` only while the macro is running.
* **Deadline Scheduling**: Eliminates sleep drift and prioritizes screen capture over UI updates.
* **Target Polling**: Capable of down to 1–2 ms scan intervals.

### 2. `auto_bid_v2.py` (Outline Trigger with Visual Tuning)
* Movable transparent HUD with real-time edge pixel counter.
* Visual slider adjustments for edge margin and threshold calibration.

### 3. `auto_bid.py` (Timing Bar & Slider Overlap)
* Designed for slider mini-games (e.g., fishing, auction biddings, lock-picking).
* Detects saturated green target zones and moving white indicator bars.
* Automatically triggers the keystroke when the indicator aligns with the target zone.

---

## Requirements

* **Operating System**: Windows 10 or 11 (64-bit)
* **Python**: Python 3.10+
* **Dependencies**:
  * `mss`: High-performance screen capture
  * `numpy`: Fast vectorized pixel array operations
  * `pynput`: Direct keyboard injection & global hotkeys
  * `Pillow`: Image processing utilities

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/millonova/Auto_Bid.git
   cd Auto_Bid
   ```

2. **Create and activate a virtual environment (optional)**:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

3. **Install dependencies**:
   ```powershell
   pip install -r requirements.txt
   ```

---

## How to Run & Use

### Running Version 2.1 (Ultra-Low Latency Outline Mode)
```powershell
python auto_bid_v2_1.py
```

### Running Version 1 (Timing Bar Overlap Mode)
```powershell
python auto_bid.py
```

### Calibration Workflow:
1. **Position the Frame**: A transparent targeting frame will appear on screen. Move and resize it directly over the game's timing bar or bid button.
2. **Configure Hotkeys & Trigger Key**: Set your desired action key (e.g., `Q`, `Space`, or `Enter`).
3. **Set Cooldown**: Set a cooldown period (e.g., `150ms`) to avoid accidental multi-triggers.
4. **Arm the Macro**: Press the designated hotkey or click **Start**. The macro will scan the designated region and trigger automatically as soon as the target visual conditions are satisfied.

---

## Settings & Configuration

Settings are saved automatically in `settings/`:
* `settings/auto_bid_settings.json`
* `settings/auto_bid_v2_settings.json`
* `settings/auto_bid_v2_1_settings.json`

Example configuration:
```json
{
  "left": 6,
  "top": 7,
  "width": 808,
  "height": 136,
  "key": "q",
  "poll_ms": 2,
  "cooldown_ms": 150,
  "minimum_edge_pixels": 550,
  "edge_band_px": 12,
  "always_on_top": true
}
```

---

## License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details. Free for personal and educational use.

---

## ⚠️ Disclaimer

Use this automation software only where permitted by the rules and terms of service of the relevant application or game. The creators accept no responsibility for account bans or penalties resulting from misuse.
