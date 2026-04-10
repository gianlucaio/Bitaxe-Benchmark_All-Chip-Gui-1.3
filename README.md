# Bitaxe Hashrate Benchmark — GUI Edition

A Python-based benchmarking tool with a graphical interface for optimizing Bitaxe mining performance. Performs a complete **2-D sweep across all voltage × frequency combinations**, monitoring hashrate, temperature, power efficiency, and ASIC error rate — with full support for both **single-chip** (5 V barrel jack) and **dual-chip** models (GT 800, GT 801, Duo 650 — 12 V XT30).

> This project is a fork of [mrv777/Bitaxe-Hashrate-Benchmark](https://github.com/mrv777/Bitaxe-Hashrate-Benchmark), which is itself a fork of [WhiteyCookie/Bitaxe-Hashrate-Benchmark](https://github.com/WhiteyCookie/Bitaxe-Hashrate-Benchmark).

---

## What's new in this fork

| Feature | Original CLI | BitaxeBenchGui v1.5 |
|---|---|---|
| Interface | Command line only | Graphical window (tkinter) — dark Bitcoin theme |
| Dual-chip support (GT 800/801, Duo 650) | ❌ | ✅ |
| Auto-detection of chip count | ❌ | ✅ Multi-signal heuristic (asicCount → keyword scan → hashrate) |
| Manual chip mode override | `--model-profile` flag | Radio button in GUI |
| Benchmark sweep | 1-D: frequency only at starting voltage | ✅ 2-D: all frequency levels at every voltage level |
| Unstable steps | Skipped silently | ✅ Recorded with `stable: false` for analysis |
| Custom PSU wattage limit | Hardcoded 40 W | ✅ Editable field |
| Custom temp / VR temp limits | Hardcoded | ✅ Editable fields |
| Custom voltage step | Hardcoded 20 mV | ✅ Editable field (5–100 mV) |
| Custom frequency step | Hardcoded 25 MHz | ✅ Editable field (5–100 MHz) |
| User-defined max voltage ceiling | Hardcoded 1400 mV | ✅ Editable field |
| User-defined max frequency ceiling | Hardcoded 1200 MHz | ✅ Editable field |
| Input voltage range | 5 V only (4800–5500 mV) | ✅ Auto-adjusted per profile (12 V for dual-chip) |
| Error rate source | `sharesRejected / sharesAccepted` (cumulative, inaccurate) | ✅ `errorPercentage` direct from AxeOS UI |
| Per-chip error tracking | ❌ | ✅ `hashrateMonitor.asics[n].errorCount` delta per sample |
| Dual temp monitoring | Single `temp` field | ✅ `temp` + `temp2` — hottest chip always the limit |
| Dual VRM monitoring | Single `vrTemp` field | ✅ `vrTemp` + `vrTemp2` — hottest always the limit |
| `asicCount` bug fix | Reports 1 on dual-chip firmware → wrong hashrate calc | ✅ Auto-corrected to 2 with warning |
| ASIC error rate analysis | ❌ | ✅ Analyse Results window with colour-coded table |
| Reset to defaults | ❌ | ✅ One-click button |
| Real-time coloured log | Terminal only | ✅ Scrollable log panel with colour coding |
| Stop benchmark mid-run | Ctrl+C only | ✅ Stop button — clean stop, applies best settings |
| JSON output | Basic | ✅ Includes `stable`, `sweep`, `averageErrorRate`, `averageVRTemp` per step |

---

## Supported models

| Model | Chip | PSU connector | Profile |
|---|---|---|---|
| Bitaxe Max (1xx) | BM1397 | 5 V barrel jack | single |
| Bitaxe Ultra (2xx) | BM1366 | 5 V barrel jack | single |
| Bitaxe Supra (4xx) | BM1368 | 5 V barrel jack | single |
| Bitaxe Gamma 6xx | BM1370 | 5 V barrel jack | single |
| **Bitaxe GT 800** | 2× BM1370 | **12 V XT30** | **dual** |
| **Bitaxe GT 801** | 2× BM1370 | **12 V XT30** | **dual** |
| **Bitaxe Duo 650** | 2× BM1370 | **12 V XT30** | **dual** |
| Bitaxe Hex (3xx / 7xx) | 6× BM1366/1368 | 12 V XT30 | dual |

---

## Prerequisites

- Python **3.11** or higher
- `tkinter` (included with most Python distributions — see below if missing)
- `requests` library (`pip install -r requirements.txt`)
- Access to a Bitaxe miner on your local network (WiFi 2.4 GHz)
- Git (optional)

### Installing tkinter if missing

```bash
# Debian / Ubuntu / Raspberry Pi OS
sudo apt install python3-tk

# Fedora
sudo dnf install python3-tkinter

# macOS (via Homebrew)
brew install python-tk

# Windows
# tkinter is bundled with the official python.org installer — no extra step needed
```

---

## Installation

```bash
# 1. Clone this repository
git clone https://github.com/<your-username>/Bitaxe-Hashrate-Benchmark.git
cd Bitaxe-Hashrate-Benchmark

# 2. Create and activate a virtual environment
python -m venv venv

# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Usage

### Launch the GUI

```bash
python BitaxeBenchGui_1_5.py
```

The window opens immediately. No command-line arguments are required.

---

## GUI walkthrough

### 1. Connection

Enter the local IP address of your Bitaxe (e.g. `192.168.1.42`).  
Find it in your router's DHCP table or on the AxeOS OLED display.

### 2. Chip detection

Choose how the tool identifies your hardware:

| Option | When to use |
|---|---|
| **Auto (recommended)** | Tries three detection signals in order: `asicCount ≥ 2` from the API → keyword scan across all string fields (`gt`, `duo`, `800`, `801`, `650`…) → live hashrate heuristic (> 1500 GH/s → dual). Works for most devices. |
| **Single chip** | Force the 5 V profile if auto-detect picks the wrong mode. |
| **Dual chip** | Force the 12 V profile — use this for GT 800/801 and Duo 650 if auto-detect fails. |

### 3. Settings

All fields are editable before each run and restored by **↺ Reset to defaults**:

| Field | Default | Range | Description |
|---|---|---|---|
| Initial voltage (mV) | 1150 | 1000–1400 | Starting core voltage. The sweep begins here. |
| Initial frequency (MHz) | 500 | 400–1200 | Starting clock frequency. The sweep begins here. |
| Max voltage (mV) | 1400 | 1000–1400 | Upper ceiling for core voltage. The sweep stops here. |
| Max frequency (MHz) | 1200 | 400–1200 | Upper ceiling for frequency. The sweep stops here. |
| PSU max wattage (W) | 60 | 10–500 | Hard power ceiling. Set this to your PSU's rated output. |
| Max chip temp (°C) | 66 | 40–90 | Per-chip cutoff. On dual-chip models the hottest chip is always used. |
| Max VR temp (°C) | 86 | 40–110 | Voltage-regulator cutoff. Monitors both `vrTemp` and `vrTemp2`. |
| Voltage step (mV) | 20 | 5–100 | Increment between voltage levels in the outer sweep loop. |
| Frequency step (MHz) | 25 | 5–100 | Increment between frequency levels in the inner sweep loop. |

> **Runtime estimate:** total combinations = `((max_v − start_v) / v_step + 1) × ((max_f − start_f) / f_step + 1)`. Each combination takes ~11.5 minutes (10 min benchmark + 90 s stabilisation). With defaults starting at 1150 mV and 500 MHz up to 1150 mV and 1200 MHz (1 voltage level, 29 frequency steps) that is approximately 5.5 hours. Adding more voltage levels multiplies the time accordingly.

### 4. Buttons

| Button | Action |
|---|---|
| **↺ Reset to defaults** | Restores all fields to factory values without clearing the log or stopping a run. |
| **▶ Start benchmark** | Validates inputs, connects to the Bitaxe, and begins the 2-D sweep. |
| **📊 Analyse Results** | Opens the analysis window — load any benchmark JSON to see the full result table and the optimal configuration highlighted. |
| **⏹ Stop benchmark** | Requests a clean stop after the current sample. Applies the best stable settings found so far and saves the JSON. |
| **🗑 Clear log** | Clears the output panel without affecting a running benchmark. |

---

## How the benchmark works

The sweep is a **2-D grid**: for each voltage level from `initial voltage` to `max voltage` (stepping by `voltage step`), every frequency from `initial frequency` to `max frequency` (stepping by `frequency step`) is tested before moving to the next voltage level.

For each combination the tool:

1. Applies the voltage and frequency via the AxeOS API (`PATCH /api/system`), then restarts the device and waits **90 seconds** for stabilisation.
2. Collects **40 samples** over **10 minutes** (one every 15 seconds), recording hashrate, temperatures, power, and error rate.
3. Validates that the average hashrate is within **6 %** of the theoretical maximum (`frequency × total_cores / 1000`).
4. Records the result with a `stable: true/false` flag regardless of whether it passed the hashrate check — unstable results are kept for the Analysis window, not discarded.
5. If a **hard safety limit** is hit (temperature, VR temperature, input voltage, or power) the remaining frequencies for that voltage level are skipped and the outer loop advances to the next voltage.

After the sweep completes, the tool automatically applies the **highest-hashrate stable configuration** found and saves the full results to a JSON file.

---

## Error rate measurement

The tool reads the error rate from the AxeOS API in this priority order:

1. **`errorPercentage`** — the same percentage displayed in the AxeOS web UI. Present in AxeOS v2.12 and later. This is the primary and most accurate source.
2. **`hashrateMonitor.asics[n].errorCount` delta** — the increment of each chip's cumulative error counter between two consecutive 15-second samples. The sum across all chips is expressed as a percentage of expected hash operations in that interval. Used as a fallback when `errorPercentage` is not available.

The fallback based on `sharesRejected / sharesAccepted` has been **removed entirely**. Those counters accumulate from device boot and are not reset between benchmark steps — they produce a meaningless average that reflects the entire uptime, not the current test window.

Error rate thresholds used by the Analysis window:

| Range | Classification |
|---|---|
| > 1.00 % | Step excluded from best-result selection |
| 0.70 %–1.00 % | Acceptable but not in the optimal window |
| 0.20 %–0.70 % | Optimal window — best balance of hashrate and stability |
| < 0.20 % | Excellent |

---

## Safety features

- **Per-chip temperature monitoring** — reads both `temp` and `temp2`; the hottest chip is always the cutoff reference.
- **Dual VRM monitoring** — reads both `vrTemp` and `vrTemp2`; the hottest is the cutoff reference.
- **Profile-aware input voltage check** — single-chip: 4800–5500 mV; dual-chip: 11800–12200 mV. Prevents false stops on 12 V boards.
- **User-defined PSU wattage limit** — benchmark stops if measured power exceeds the value set in the GUI.
- **User-defined max voltage and frequency ceilings** — the sweep never exceeds what you set, regardless of the absolute hardware limits.
- **Temperature floor** — readings below 5 °C are rejected as sensor errors.
- **Outlier removal** — 3 highest and 3 lowest hashrate samples are discarded before averaging.
- **Warmup exclusion** — first 6 temperature readings per step are excluded to avoid cold-start bias.
- **Hashrate sanity check** — average hashrate must be ≥ 94 % of the theoretical maximum; steps below this threshold are marked `stable: false`.
- **Graceful stop** — pressing Stop applies the best stable settings found so far before exiting.
- **Dual-chip `asicCount` correction** — if the firmware incorrectly reports `asicCount=1` on a dual-chip board, the tool detects the mismatch via the active profile and forces `asicCount=2` for hashrate calculations, logging a warning.

---

## Output

Results are saved to:

```
bitaxe_benchmark_<ip>_<timestamp>.json
```

The JSON structure:

```json
{
  "profile": "Dual-chip (GT 800/801, Duo 650 — 12V XT30)",
  "sweep": "2D voltage × frequency",
  "all_results": [
    {
      "coreVoltage": 1150,
      "frequency": 500,
      "averageHashRate": 2412.3,
      "averageTemperature": 58.1,
      "averageVRTemp": 54.2,
      "efficiencyJTH": 14.8,
      "averageErrorRate": 0.42,
      "stable": true,
      "profile": "Dual-chip (GT 800/801, Duo 650 — 12V XT30)"
    }
  ],
  "top_performers": [ ... ],
  "most_efficient": [ ... ]
}
```

`top_performers` and `most_efficient` contain the top 5 entries filtered to stable results only. If no stable step was recorded they fall back to the full result set.

---

## Configuration reference

### GUI fields (editable before each run, restored by Reset to defaults)

| Field | Default | Range | Description |
|---|---|---|---|
| Initial voltage | 1150 mV | 1000–1400 mV | Starting core voltage |
| Initial frequency | 500 MHz | 400–1200 MHz | Starting clock frequency |
| Max voltage | 1400 mV | 1000–1400 mV | Sweep ceiling for voltage |
| Max frequency | 1200 MHz | 400–1200 MHz | Sweep ceiling for frequency |
| PSU max wattage | 60 W | 10–500 W | Power draw ceiling |
| Max chip temp | 66 °C | 40–90 °C | Per-chip temperature cutoff |
| Max VR temp | 86 °C | 40–110 °C | Voltage-regulator temperature cutoff |
| Voltage step | 20 mV | 5–100 mV | Outer loop increment |
| Frequency step | 25 MHz | 5–100 MHz | Inner loop increment |

### Fixed constants (edit in source if needed)

| Constant | Value | Description |
|---|---|---|
| `SLEEP_TIME` | 90 s | Stabilisation wait after each restart |
| `BENCHMARK_TIME` | 600 s | Duration per combination (10 min) |
| `SAMPLE_INTERVAL` | 15 s | Time between samples |
| `ERR_MAX_VALID` | 1.0 % | Error rate above which a step is excluded from best-result selection |
| `ERR_OPT_LOW` | 0.20 % | Lower bound of the optimal error-rate window |
| `ERR_OPT_HIGH` | 0.70 % | Upper bound of the optimal error-rate window |
| `SINGLE_CHIP_VMIN/VMAX` | 4800–5500 mV | Input voltage range for 5 V barrel jack models |
| `DUAL_CHIP_VMIN/VMAX` | 11800–12200 mV | Input voltage range for 12 V XT30 models |
| `DUAL_CHIP_HASHRATE_THRESHOLD_GHS` | 1500 GH/s | Live hashrate above which auto-detect infers dual-chip |

---

## Data processing details

- **Hashrate outlier removal** — 3 highest and 3 lowest readings removed; average computed on the remaining trimmed set.
- **Temperature warmup exclusion** — first 6 readings per step discarded before averaging; same applied to VR temperature.
- **Error rate** — all samples averaged without trimming; first sample excluded when using the per-chip delta method (no previous snapshot available for the delta).
- **Power** — all samples averaged without trimming.
- **Efficiency** — `avg_power_W / (avg_hashrate_GHs / 1000)` → J/TH.
- **Hashrate validation** — average must be ≥ 94 % of `frequency × (smallCoreCount × asicCount) / 1000`.

---

## Contributing

Contributions are welcome. Please open an issue first to discuss what you would like to change, then submit a Pull Request against the `main` branch.

If you own a model not yet listed in the supported table, sharing your API response (especially `ASICModel`, `boardVersion`, `asicCount`, and the `hashrateMonitor` structure) is very helpful for improving auto-detection accuracy and error-rate measurement.

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.

---

## Disclaimer

This tool runs your Bitaxe outside its factory parameters. Overclocking and voltage modifications can damage hardware if done without adequate cooling or with an undersized power supply. Always set the PSU wattage limit correctly in the GUI before starting. The authors accept no responsibility for hardware damage.
