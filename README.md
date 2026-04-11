# ⛏ BitaxeBenchGui v1.6

**Bitaxe All-Model Hashrate Benchmark — GUI Edition**

A dark-themed, Bitcoin-orange desktop tool for systematically benchmarking every voltage × frequency combination on your Bitaxe ASIC miner and finding the optimal configuration automatically.

Supports **single-chip models** (Gamma, Supra, Ultra — 5 V barrel jack) and **dual-chip models** (GT 800/801, Duo 650 — 12 V XT30).

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.11+ | `python3 --version` |
| `requests` | `pip install requests` |
| `tkinter` | Usually bundled. Linux: `sudo apt install python3-tk` |
| Bitaxe running AxeOS | Firmware 2.x recommended (exposes `errorPercentage`) |

No other dependencies. All other modules used (`csv`, `json`, `threading`, `queue`, `math`, `os`) are part of the Python standard library.

---

## Quick Start

```bash
python3 BitaxeBenchGui_1_6.py
```

1. Enter the Bitaxe IP address (e.g. `192.168.1.100`)
2. Set your starting voltage, frequency, steps and ceilings
3. Press **▶ Start Benchmark**
4. When finished, the best configuration is applied automatically and results are saved as JSON + CSV

---

## GUI Overview

### Configuration panel

| Field | Description |
|---|---|
| **Bitaxe IP address** | Local IP of the miner (no `http://`) |
| **Chip detection** | Auto (recommended), Single chip, or Dual chip |
| **Initial voltage (mV)** | Starting core voltage for the sweep |
| **Initial frequency (MHz)** | Starting clock frequency for the sweep |
| **PSU max wattage (W)** | Safety ceiling — benchmark stops the step if exceeded |
| **Max chip temp (°C)** | Safety ceiling for die temperature |
| **Max VR temp (°C)** | Safety ceiling for VR temperature |
| **Voltage step (mV)** | Increment between voltage levels |
| **Frequency step (MHz)** | Increment between frequency steps |
| **Max voltage (mV)** | Upper bound for the voltage sweep |
| **Max frequency (MHz)** | Upper bound for the frequency sweep |
| **Max error rate (%)** | Steps above this threshold are flagged as discarded in analysis (default 1.0 %) |
| **Early-stop steps** | If hashrate drops for N consecutive frequency steps, skip to next voltage (0 = disabled) |
| **Adaptive warm-up** | ✓ = wait for temperature to stabilise instead of fixed 40 s timer |

### Buttons

| Button | Action |
|---|---|
| **↺ Reset** | Restore all fields to defaults |
| **▶ Start Benchmark** | Validate settings and start a fresh sweep |
| **⏭ Resume** | Load a partial JSON from a previous interrupted run and continue from where it stopped |
| **📊 Analyse Results** | Open the analysis window for any benchmark JSON |
| **📄 Export CSV** | Export current or loaded results to a CSV file |
| **⏹ Stop Benchmark** | Gracefully stop after the current sample finishes |

### Progress bar & ETA

A progress bar below the status line shows percentage complete and estimated time remaining, calculated from the number of tested steps vs total planned steps (voltage levels × frequency levels).

### Live hashrate chart

A real-time sparkline updates every sample interval during the benchmark, showing the hashrate trend across all steps tested so far.

---

## How the Benchmark Works

### 2-D voltage × frequency sweep (v1.5+)

For each voltage level from `start_v` to `max_v` (stepping by `v_step`):

1. Reset frequency to `start_f`
2. Apply `voltage / frequency` to the device via PATCH `/api/system`
3. Restart the device
4. Wait for warm-up (adaptive or fixed)
5. Sample the device every 15 s for ~500 s total (~33 samples)
6. Record hashrate, temperature, VR temp, power, error rate
7. Advance frequency by `f_step` and repeat from step 2
8. After finishing all frequencies, advance voltage and repeat the whole inner loop

Every combination is tested independently. An unstable hashrate does **not** abort the frequency sweep — it is recorded with `stable: false` and the next step continues.

### Safety limits (abort current step)

If any of these are exceeded during sampling, the current voltage level is abandoned and the benchmark moves to the next voltage:

- Chip temperature ≥ Max chip temp
- VR temperature ≥ Max VR temp
- Power draw > PSU max wattage
- Input voltage outside valid range for the detected profile
- Hashrate or power data unavailable from API

### Adaptive warm-up (v1.6)

When enabled, instead of waiting a fixed 40 s after restart, the tool polls the chip temperature every 10 s and waits until two consecutive readings differ by less than ±1 °C. Always waits at least 20 s for the device to boot. Falls back to a 80 s ceiling if temperature never stabilises.

### Early-stop on declining hashrate (v1.6)

If `Early-stop steps` is set to N > 0, the frequency sweep for a given voltage level stops early when hashrate declines for N consecutive steps. This avoids wasting time on clearly declining regions of the frequency space and moves to the next voltage faster.

### Resume (v1.6)

If a benchmark run is interrupted (power cut, crash, manual stop), press **⏭ Resume**, select the partial JSON file, and the benchmark will skip all already-tested `(voltage, frequency)` pairs and continue from the first untested combination.

---

## Output Files

Two files are saved automatically when the benchmark finishes or is stopped:

### JSON — `bitaxe_benchmark_<ip>_<datetime>.json`

```json
{
  "profile": "Dual-chip (GT 800/801, Duo 650 — 12V XT30)",
  "sweep": "2D voltage × frequency",
  "all_results": [
    {
      "coreVoltage": 1230,
      "frequency": 650,
      "averageHashRate": 2650.8,
      "averageTemperature": 61.4,
      "efficiencyJTH": 18.47,
      "profile": "...",
      "stable": true,
      "averageVRTemp": 58.0,
      "averageErrorRate": 0.702
    },
    ...
  ],
  "top_performers": [...],
  "most_efficient": [...]
}
```

### CSV — `bitaxe_benchmark_<ip>_<datetime>.csv`

Same data as `all_results` in tabular form, directly openable in Excel or LibreOffice Calc. Columns: `coreVoltage`, `frequency`, `averageHashRate`, `averageTemperature`, `efficiencyJTH`, `stable`, `averageVRTemp`, `averageErrorRate`, `profile`.

---

## Analysis Window

Open via **📊 Analyse Results**. Two tabs:

### Tab 1 — Results Table

Full table of every tested step, colour-coded by error rate:

| Colour | Meaning |
|---|---|
| 🟢 Dark green | Optimal — error rate 0.20–0.70 % |
| 🟠 Dark orange | Acceptable — error rate 0.70–1.00 % |
| 🔴 Dark red | Discarded — error rate > 1.00 % |
| 🟡 Gold | ★ Best step |
| ⬛ Dark blue | No error data from firmware |

The **★ Best** step is selected by preferring the optimal window first, then acceptable steps, sorted by lowest J/TH (best efficiency). If no error data is available, it falls back to the overall best efficiency.

### Tab 2 — Heatmap 🔥

An interactive voltage × frequency grid where each cell is coloured by either:

- **Hashrate (GH/s)** — blue = low, red = high
- **Efficiency (J/TH)** — red = low (best), blue = high (worst)

Toggle between modes with the radio buttons. Cell values are printed inside each cell when the grid is large enough. A colour scale bar is shown on the right edge.

---

## Error Rate Sources

The tool reads error rate in priority order:

1. `errorPercentage` — direct AxeOS field, matches the dashboard UI exactly (AxeOS 2.12+)
2. `asicErrorRate` — older field name used in some firmware forks
3. Delta of `hashrateMonitor.asics[n].errorCount` between consecutive samples — per-chip increment, used as fallback when neither field is available

Cumulative `sharesRejected / sharesAccepted` are intentionally **not used** — they grow from boot and are meaningless within a single benchmark step.

---

## Supported Models

| Model | Profile | Input voltage |
|---|---|---|
| Gamma, Supra, Ultra | Single-chip | 4.8–5.5 V |
| GT 800, GT 801 | Dual-chip | 11.8–12.2 V |
| Duo 650 | Dual-chip | 11.8–12.2 V |

Model detection is automatic (reads `asicCount` and API string fields). Can be overridden manually in the GUI.

---

## Changelog

### v1.6
- Progress bar with step count and ETA
- Live hashrate sparkline chart during benchmark
- Early-stop on consecutive declining hashrate steps (configurable)
- Adaptive warm-up: waits for temperature stability instead of fixed timer
- Resume from partial JSON: skip already-tested combinations
- CSV export (automatic on finish + manual export button)
- Configurable error-rate threshold in GUI
- Completion sound (system bell / winsound)
- Heatmap tab in Analysis window (hashrate or J/TH, togglable)

### v1.5
- Fixed 2-D voltage × frequency sweep (old code was 1-D diagonal)
- Fixed error rate: reads `errorPercentage` directly from AxeOS
- Per-chip `errorCount` delta as secondary error source
- Unstable steps recorded instead of silently skipped
- `_apply_best` and `_print_summary` prefer stable results
- JSON includes `sweep` field and `stable` boolean per entry

### v1.4
- `max_voltage` and `max_frequency` fields in GUI

### v1.3
- Error-rate sampling every iteration
- `averageErrorRate` in JSON output
- Analysis window with colour-coded table and best-step card
- Dark Bitcoin-themed GUI

---

## Disclaimer

Overclocking may damage hardware. Voltages above the manufacturer's recommended range can permanently degrade or destroy the ASIC chip. Use this tool at your own risk. Start conservatively and increase limits gradually.
