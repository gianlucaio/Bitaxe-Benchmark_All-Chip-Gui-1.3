"""
Bitaxe All Model Hashrate Benchmark — GUI Edition  v1.8
Supports single-chip (5V) and dual-chip models (GT 800/801, Duo 650 — 12V XT30).

New in v1.8
  • Network Scanner: auto-discover Bitaxe devices on local network (10.x.x.x)
  • Monitor Control: real-time monitoring window without AXE OS

New in v1.7
  • Bug fixes:
    - Division by zero protection in efficiency calculation
    - Timeout accumulo: max 3 global retries instead of per-request
    - Chart overflow: limit to last 200 data points
  • Auto-save every N steps: configurable interval (default 10), saves partial
    JSON during benchmark so long runs can be resumed if interrupted
  • Preset profiles: save/load entire configuration with custom names
  • Export Markdown: generate GitHub-flavored .md report with tables & summary
  • Safety auto-stop: if temp rises >3°C in 30s, pause & retry; abort if happens twice
  • Comparison mode: load two JSONs side-by-side in analysis window
  • Heatmap click details: click any cell in heatmap to see full step info in popup

New in v1.6 (carried over)
  • Progress bar with ETA
  • Live hashrate chart
  • Early-stop on declining hashrate
  • Adaptive warm-up
  • Resume from partial JSON
  • CSV export
  • Configurable error-rate threshold
  • Completion sound
  • Heatmap in Analysis window
"""

import requests
import time
import json
import sys
import math
import threading
import queue
import csv
import os
import socket
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

try:
    import winsound
    _HAS_WINSOUND = True
except ImportError:
    _HAS_WINSOUND = False

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox, filedialog, simpledialog
except ImportError:
    print("ERROR: tkinter not found. Install it with: sudo apt install python3-tk")
    sys.exit(1)

# ---------------------------------------------------------------------------
# DEFAULTS — shown in GUI, user can edit before starting
# ---------------------------------------------------------------------------
DEFAULTS = {
    "ip":                  "",
    "voltage":             1150,
    "frequency":           500,
    "max_psu_watts":       60,
    "max_temp":            66,
    "max_vr_temp":         86,
    "chip_mode":           "auto",
    "voltage_increment":   20,
    "frequency_increment": 25,
    "max_voltage":         1400,
    "max_frequency":       1200,
    "err_max_valid":       1.0,
    "early_stop_steps":    3,
    "adaptive_warmup":     True,
    "autosave_interval":   10,      # v1.7: auto-save every N steps
}

# Benchmark constants
VOLTAGE_INCREMENT    = DEFAULTS["voltage_increment"]
FREQUENCY_INCREMENT  = DEFAULTS["frequency_increment"]
SLEEP_TIME           = 40
BENCHMARK_TIME       = 500
SAMPLE_INTERVAL      = 15
MAX_ALLOWED_VOLTAGE  = 1400
MIN_ALLOWED_VOLTAGE  = 1000
MAX_ALLOWED_FREQ     = 1200
MIN_ALLOWED_FREQ     = 400

SINGLE_CHIP_VMIN = 4800
SINGLE_CHIP_VMAX = 5500
DUAL_CHIP_VMIN   = 11800
DUAL_CHIP_VMAX   = 12200

DUAL_CHIP_KEYWORDS = ["gt", "duo", "800", "801", "650", "dual", "2chip"]
DUAL_CHIP_HASHRATE_THRESHOLD_GHS = 1500

# Error-rate thresholds (%)
ERR_MAX_VALID   = 1.0
ERR_OPT_LOW     = 0.20
ERR_OPT_HIGH    = 0.70

# v1.7: Safety auto-stop thresholds
SAFETY_TEMP_RISE_THRESHOLD = 3.0   # °C increase in monitoring window
SAFETY_TEMP_WINDOW_SECS    = 30    # seconds to measure temp rise
SAFETY_MAX_VIOLATIONS      = 2     # abort benchmark after N violations

# v1.7: Chart data limit (prevent memory/performance issues)
CHART_MAX_POINTS = 200

# v1.8: Network scanner settings
NETWORK_SCAN_SUBNET = "10.0.0."
NETWORK_SCAN_RANGE  = (1, 254)
NETWORK_SCAN_TIMEOUT = 0.5

# ---------------------------------------------------------------------------
# Colour palette (dark Bitcoin theme)
# ---------------------------------------------------------------------------
C = {
    "bg":          "#111827",
    "panel":       "#1f2937",
    "card":        "#374151",
    "accent":      "#f7931a",
    "accent_dark": "#c4760e",
    "text":        "#f3f4f6",
    "muted":       "#9ca3af",
    "green":       "#22c55e",
    "yellow":      "#f59e0b",
    "red":         "#ef4444",
    "blue":        "#3b82f6",
    "log_bg":      "#0d1117",
    "separator":   "#374151",
}


# ---------------------------------------------------------------------------
# v1.7: Preset management
# ---------------------------------------------------------------------------

class PresetManager:
    """Manage save/load of benchmark configuration presets."""

    PRESETS_DIR = Path.home() / ".bitaxe_bench_presets"

    @classmethod
    def ensure_dir(cls):
        cls.PRESETS_DIR.mkdir(exist_ok=True)

    @classmethod
    def save_preset(cls, name: str, config: dict):
        cls.ensure_dir()
        path = cls.PRESETS_DIR / f"{name}.json"
        with open(path, 'w') as f:
            json.dump(config, f, indent=2)

    @classmethod
    def load_preset(cls, name: str) -> dict | None:
        path = cls.PRESETS_DIR / f"{name}.json"
        if not path.exists():
            return None
        with open(path, 'r') as f:
            return json.load(f)

    @classmethod
    def list_presets(cls) -> list[str]:
        cls.ensure_dir()
        return sorted([p.stem for p in cls.PRESETS_DIR.glob("*.json")])

    @classmethod
    def delete_preset(cls, name: str):
        path = cls.PRESETS_DIR / f"{name}.json"
        if path.exists():
            path.unlink()


# ---------------------------------------------------------------------------
# v1.8: Network Scanner
# ---------------------------------------------------------------------------

class NetworkScanner:
    """Scan local network for Bitaxe devices."""

    @classmethod
    def get_local_network(cls) -> str:
        """Get local network prefix (e.g., '10.0.0.')"""
        try:
            # Try to get local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            # Extract network prefix (first 3 octets)
            parts = local_ip.split('.')
            if len(parts) == 4:
                return f"{parts[0]}.{parts[1]}.{parts[2]}."
        except Exception:
            pass
        return NETWORK_SCAN_SUBNET

    @classmethod
    def scan(cls, progress_callback=None, result_callback=None):
        """
        Scan network for responsive devices that might be Bitaxe.
        Returns list of (ip, response_time) tuples.
        """
        subnet = cls.get_local_network()
        found_devices = []

        def check_ip(ip):
            try:
                start = time.time()
                # Try to connect to Bitaxe API port
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(NETWORK_SCAN_TIMEOUT)
                result = sock.connect_ex((ip, 80))
                sock.close()
                if result == 0:
                    response_time = (time.time() - start) * 1000
                    return (ip, response_time)
            except Exception:
                pass
            return None

        # Scan in parallel using threads
        threads = []
        results_lock = threading.Lock()

        def scan_worker(start_ip, end_ip):
            for i in range(start_ip, end_ip + 1):
                ip = f"{subnet}{i}"
                result = check_ip(ip)
                if result:
                    with results_lock:
                        found_devices.append(result)
                if progress_callback:
                    progress_callback(i)

        # Split scan into chunks for parallel execution
        chunk_size = 50
        for start in range(NETWORK_SCAN_RANGE[0], NETWORK_SCAN_RANGE[1] + 1, chunk_size):
            end = min(start + chunk_size - 1, NETWORK_SCAN_RANGE[1])
            t = threading.Thread(target=scan_worker, args=(start, end))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Sort by response time
        found_devices.sort(key=lambda x: x[1])

        if result_callback:
            result_callback(found_devices)

        return found_devices

    @classmethod
    def verify_bitaxe(cls, ip: str) -> dict | None:
        """Verify if IP is a Bitaxe device by checking API."""
        try:
            r = requests.get(f"http://{ip}/api/system/info", timeout=2)
            if r.status_code == 200:
                data = r.json()
                if "smallCoreCount" in data or "asicCount" in data:
                    return data
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# v1.8: Monitor Control Window
# ---------------------------------------------------------------------------

class MonitorControlWindow(tk.Toplevel):
    """Real-time monitoring window for Bitaxe device."""

    def __init__(self, parent, ip: str = ""):
        super().__init__(parent)
        self.title("📡 Bitaxe Monitor Control v1.8")
        self.configure(bg=C["bg"])
        self.resizable(True, True)
        self.minsize(900, 600)

        self._bitaxe_ip = ip
        self._monitor_running = False
        self._monitor_thread = None
        self._monitor_queue = queue.Queue()
        self._update_interval = 1  # seconds

        self._build_ui()
        self._poll_queue()

        # Auto-start if IP provided
        if ip:
            self._start_monitor()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=C["accent"], height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(
            hdr, text="📡 BITAXE MONITOR CONTROL",
            bg=C["accent"], fg="#111827",
            font=("Courier", 14, "bold"),
        ).pack(side="left", padx=16, pady=10)

        # IP entry
        ip_frame = tk.Frame(hdr, bg=C["accent"])
        ip_frame.pack(side="right", padx=16)

        tk.Label(
            ip_frame, text="Device IP:",
            bg=C["accent"], fg="#78350f",
            font=("Courier", 9),
        ).pack(side="left", padx=(0, 5))

        self._ip_var = tk.StringVar(value=self._bitaxe_ip)
        ip_entry = ttk.Entry(
            ip_frame, textvariable=self._ip_var,
            width=18, font=("Courier", 9)
        )
        ip_entry.pack(side="left")

        ttk.Button(
            ip_frame, text="Connect",
            command=self._connect,
            style="Accent.TButton"
        ).pack(side="left", padx=(10, 0))

        # Status bar
        self._status_var = tk.StringVar(value="Disconnected")
        status_bar = tk.Label(
            hdr, textvariable=self._status_var,
            bg=C["accent"], fg="#78350f",
            font=("Courier", 9),
        )
        status_bar.pack(side="right", padx=16)

        # Main content
        main_frame = tk.Frame(self, bg=C["bg"])
        main_frame.pack(fill="both", expand=True, padx=14, pady=10)
        main_frame.columnconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)

        # Left panel - Live values
        left_panel = ttk.LabelFrame(main_frame, text="  📊 Live Values  ", padding=10)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 7))

        self._values_text = scrolledtext.ScrolledText(
            left_panel, height=20, width=45,
            font=("Courier", 9), bg=C["log_bg"], fg=C["text"],
            state="disabled", wrap="word"
        )
        self._values_text.pack(fill="both", expand=True)

        # Right panel - Controls
        right_panel = ttk.LabelFrame(main_frame, text="  ⚙️ Controls  ", padding=10)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        # Voltage control
        ttk.Label(right_panel, text="Voltage (mV):", font=("Courier", 9, "bold")).pack(anchor="w", pady=(0, 2))
        self._ctrl_voltage = tk.StringVar(value="1150")
        voltage_spin = ttk.Spinbox(
            right_panel, from_=1000, to=1400,
            textvariable=self._ctrl_voltage, width=10
        )
        voltage_spin.pack(anchor="w", pady=(0, 10))

        # Frequency control
        ttk.Label(right_panel, text="Frequency (MHz):", font=("Courier", 9, "bold")).pack(anchor="w", pady=(0, 2))
        self._ctrl_frequency = tk.StringVar(value="500")
        freq_spin = ttk.Spinbox(
            right_panel, from_=400, to=1200,
            textvariable=self._ctrl_frequency, width=10
        )
        freq_spin.pack(anchor="w", pady=(0, 10))

        # Apply button
        ttk.Button(
            right_panel, text="Apply Settings",
            command=self._apply_settings,
            style="Accent.TButton"
        ).pack(fill="x", pady=(0, 15))

        # Restart button
        ttk.Button(
            right_panel, text="Restart Device",
            command=self._restart_device,
            style="Danger.TButton"
        ).pack(fill="x", pady=(0, 15))

        # Info panel
        info_panel = ttk.LabelFrame(main_frame, text="  ℹ️ Device Info  ", padding=10)
        info_panel.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        self._info_text = scrolledtext.ScrolledText(
            info_panel, height=8, width=100,
            font=("Courier", 8), bg=C["log_bg"], fg=C["text"],
            state="disabled", wrap="word"
        )
        self._info_text.pack(fill="x")

        # Bottom controls
        btn_frame = tk.Frame(main_frame, bg=C["bg"])
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(10, 0))

        self._start_monitor_btn = ttk.Button(
            btn_frame, text="▶ Start Monitoring",
            command=self._start_monitor,
            style="Accent.TButton"
        )
        self._start_monitor_btn.pack(side="left", padx=(0, 10))

        self._stop_monitor_btn = ttk.Button(
            btn_frame, text="⏹ Stop Monitoring",
            command=self._stop_monitor,
            state="disabled",
            style="Danger.TButton"
        )
        self._stop_monitor_btn.pack(side="left")

        ttk.Button(
            btn_frame, text="🔄 Refresh",
            command=self._refresh_data
        ).pack(side="left", padx=(10, 0))

        ttk.Button(
            btn_frame, text="❌ Close",
            command=self.destroy
        ).pack(side="right")

    def _connect(self):
        """Connect to Bitaxe device."""
        ip = self._ip_var.get().strip()
        if not ip:
            messagebox.showerror("Error", "Please enter a valid IP address.", parent=self)
            return

        self._status_var.set("Connecting...")
        self._update_status("Connecting...", "yellow")

        # Test connection
        try:
            r = requests.get(f"http://{ip}/api/system/info", timeout=3)
            if r.status_code == 200:
                data = r.json()
                self._bitaxe_ip = ip
                self._status_var.set(f"Connected: {ip}")
                self._update_status(f"✓ Connected to {ip}", "green")
                self._populate_device_info(data)
                self._refresh_data()
            else:
                self._update_status("✗ Connection failed", "red")
        except Exception as e:
            self._update_status(f"✗ Error: {e}", "red")

    def _populate_device_info(self, data: dict):
        """Populate device info panel."""
        self._info_text.config(state="normal")
        self._info_text.delete("1.0", "end")

        info_lines = [
            f"Device Information:",
            f"=" * 50,
            f"IP Address:        {self._bitaxe_ip}",
            f"Model:             {data.get('model', 'Unknown')}",
            f"ASIC Count:        {data.get('asicCount', 'N/A')}",
            f"Small Core Count:  {data.get('smallCoreCount', 'N/A')}",
            f"Current Voltage:   {data.get('coreVoltage', 'N/A')} mV",
            f"Current Frequency: {data.get('frequency', 'N/A')} MHz",
            f"Hashrate:          {data.get('hashRate', 0):.1f} GH/s",
            f"Temperature:       {data.get('temp', 'N/A')}°C",
            f"VR Temperature:    {data.get('vrTemp', 'N/A')}°C",
            f"Power:             {data.get('power', 0):.1f} W",
            f"Error Rate:        {data.get('errorPercentage', 'N/A')}%",
            f"Uptime:            {data.get('uptime', 'N/A')}s",
        ]

        self._info_text.insert("end", "\n".join(info_lines))
        self._info_text.config(state="disabled")

        # Update control values
        if "coreVoltage" in data:
            self._ctrl_voltage.set(str(data["coreVoltage"]))
        if "frequency" in data:
            self._ctrl_frequency.set(str(data["frequency"]))

    def _refresh_data(self):
        """Fetch and display current data."""
        if not self._bitaxe_ip:
            return

        try:
            r = requests.get(f"http://{self._bitaxe_ip}/api/system/info", timeout=2)
            if r.status_code == 200:
                data = r.json()
                self._populate_live_values(data)
        except Exception as e:
            self._update_status(f"✗ Refresh error: {e}", "red")

    def _populate_live_values(self, data: dict):
        """Update live values display."""
        self._values_text.config(state="normal")
        self._values_text.delete("1.0", "end")

        ts = datetime.now().strftime("%H:%M:%S")

        lines = [
            f"[{ts}] Live Monitoring",
            f"=" * 50,
            f"",
            f"🔋 POWER & PERFORMANCE",
            f"  Hashrate:      {data.get('hashRate', 0):.2f} GH/s",
            f"  Power:         {data.get('power', 0):.1f} W",
            f"  Efficiency:    {self._calc_efficiency(data):.3f} J/TH",
            f"",
            f"🌡️ TEMPERATURES",
            f"  Chip Temp:     {data.get('temp', 'N/A')}°C",
            f"  Chip Temp 2:   {data.get('temp2', 'N/A')}°C",
            f"  VR Temp:       {data.get('vrTemp', 'N/A')}°C",
            f"  VR Temp 2:     {data.get('vrTemp2', 'N/A')}°C",
            f"",
            f"⚙️ SETTINGS",
            f"  Voltage:       {data.get('coreVoltage', 'N/A')} mV",
            f"  Frequency:     {data.get('frequency', 'N/A')} MHz",
            f"  Input Voltage: {data.get('voltage', 'N/A')} mV",
            f"",
            f"⚠️ ERRORS",
            f"  Error Rate:    {data.get('errorPercentage', 'N/A')}%",
            f"  Errors:        {data.get('errors', 'N/A')}",
            f"",
            f"📡 NETWORK",
            f"  Uptime:        {data.get('uptime', 0)}s",
            f"  WiFi Signal:   {data.get('wifiSignal', 'N/A')} dBm",
        ]

        # Add ASIC info if available
        asic = data.get('asic', {})
        if asic:
            lines.extend([
                f"",
                f"🔧 ASIC DETAILS",
                f"  Default Voltage:  {asic.get('defaultVoltage', 'N/A')} mV",
                f"  Default Freq:     {asic.get('defaultFrequency', 'N/A')} MHz",
            ])

        self._values_text.insert("end", "\n".join(lines))
        self._values_text.config(state="disabled")

    def _calc_efficiency(self, data: dict) -> float:
        """Calculate efficiency in J/TH."""
        hr = data.get('hashRate', 0)
        pwr = data.get('power', 0)
        if hr > 0:
            return pwr / (hr / 1000)
        return 0.0

    def _apply_settings(self):
        """Apply voltage and frequency settings."""
        if not self._bitaxe_ip:
            messagebox.showerror("Error", "Not connected to device.", parent=self)
            return

        try:
            voltage = int(self._ctrl_voltage.get())
            frequency = int(self._ctrl_frequency.get())

            r = requests.patch(
                f"http://{self._bitaxe_ip}/api/system",
                json={"coreVoltage": voltage, "frequency": frequency},
                timeout=5
            )

            if r.status_code == 200:
                self._update_status(f"✓ Settings applied: {voltage}mV / {frequency}MHz", "green")
                messagebox.showinfo("Success", f"Settings applied!\nVoltage: {voltage}mV\nFrequency: {frequency}MHz", parent=self)
            else:
                self._update_status("✗ Failed to apply settings", "red")
                messagebox.showerror("Error", "Failed to apply settings.", parent=self)
        except Exception as e:
            self._update_status(f"✗ Error: {e}", "red")
            messagebox.showerror("Error", f"Error applying settings:\n{e}", parent=self)

    def _restart_device(self):
        """Restart the Bitaxe device."""
        if not self._bitaxe_ip:
            messagebox.showerror("Error", "Not connected to device.", parent=self)
            return

        if messagebox.askyesno("Confirm Restart", "Are you sure you want to restart the device?", parent=self):
            try:
                r = requests.post(f"http://{self._bitaxe_ip}/api/system/restart", timeout=5)
                if r.status_code == 200:
                    self._update_status("✓ Restart command sent", "yellow")
                    messagebox.showinfo("Restart", "Restart command sent. Device will reconnect automatically.", parent=self)
                else:
                    self._update_status("✗ Failed to restart", "red")
            except Exception as e:
                self._update_status(f"✗ Error: {e}", "red")
                messagebox.showerror("Error", f"Error sending restart:\n{e}", parent=self)

    def _start_monitor(self):
        """Start real-time monitoring."""
        if not self._bitaxe_ip:
            messagebox.showerror("Error", "Please connect to a device first.", parent=self)
            return

        self._monitor_running = True
        self._start_monitor_btn.config(state="disabled")
        self._stop_monitor_btn.config(state="normal")
        self._update_status("Monitoring active...", "green")

        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _stop_monitor(self):
        """Stop real-time monitoring."""
        self._monitor_running = False
        self._start_monitor_btn.config(state="normal")
        self._stop_monitor_btn.config(state="disabled")
        self._update_status("Monitoring stopped", "yellow")

    def _monitor_loop(self):
        """Background monitoring loop."""
        while self._monitor_running:
            try:
                r = requests.get(f"http://{self._bitaxe_ip}/api/system/info", timeout=2)
                if r.status_code == 200:
                    data = r.json()
                    self._monitor_queue.put(data)
            except Exception:
                pass
            time.sleep(self._update_interval)

    def _poll_queue(self):
        """Poll monitor queue for updates."""
        try:
            while True:
                data = self._monitor_queue.get_nowait()
                self._populate_live_values(data)
                self._update_status(f"📊 HR: {data.get('hashRate', 0):.1f} GH/s | T: {data.get('temp', 0)}°C", "green")
        except queue.Empty:
            pass
        self.after(500, self._poll_queue)

    def _update_status(self, msg: str, color: str = "white"):
        """Update status display."""
        # This could update a status label if needed
        pass


# ---------------------------------------------------------------------------
# v1.7: Network Scanner Window
# ---------------------------------------------------------------------------

class NetworkScannerWindow(tk.Toplevel):
    """Window for scanning network and discovering Bitaxe devices."""

    def __init__(self, parent, on_select_callback=None):
        super().__init__(parent)
        self.title("🔍 Network Scanner v1.8")
        self.configure(bg=C["bg"])
        self.resizable(True, False)
        self.minsize(600, 400)

        self._on_select = on_select_callback
        self._scanning = False
        self._found_devices = []

        self._build_ui()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=C["accent"], height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="🔍 NETWORK SCANNER",
            bg=C["accent"], fg="#111827",
            font=("Courier", 14, "bold"),
        ).pack(side="left", padx=16, pady=10)

        # Network info
        net_info = tk.Frame(hdr, bg=C["accent"])
        net_info.pack(side="right", padx=16)
        tk.Label(
            net_info, text=f"Scanning: {NetworkScanner.get_local_network()}x",
            bg=C["accent"], fg="#78350f",
            font=("Courier", 9),
        ).pack(side="left")

        # Controls
        ctrl_frame = tk.Frame(self, bg=C["bg"])
        ctrl_frame.pack(fill="x", padx=14, pady=10)

        ttk.Button(
            ctrl_frame, text="🔍 Scan Network",
            command=self._start_scan,
            style="Accent.TButton"
        ).pack(side="left")

        ttk.Button(
            ctrl_frame, text="🔄 Refresh",
            command=self._refresh_list
        ).pack(side="left", padx=(10, 0))

        self._scan_status = tk.Label(
            ctrl_frame, text="Ready",
            bg=C["bg"], fg=C["muted"],
            font=("Courier", 9),
        )
        self._scan_status.pack(side="right")

        # Results list
        list_frame = ttk.LabelFrame(self, text="  Found Devices  ", padding=10)
        list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        cols = ("IP Address", "Response (ms)", "Status", "Actions")
        self._tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=12)

        self._tree.heading("IP Address", text="IP Address")
        self._tree.heading("Response (ms)", text="Response (ms)")
        self._tree.heading("Status", text="Status")
        self._tree.heading("Actions", text="Actions")

        self._tree.column("IP Address", width=120, anchor="center")
        self._tree.column("Response (ms)", width=100, anchor="center")
        self._tree.column("Status", width=150, anchor="center")
        self._tree.column("Actions", width=100, anchor="center")

        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)

        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._tree.tag_configure("good", foreground=C["green"])
        self._tree.tag_configure("slow", foreground=C["yellow"])
        self._tree.tag_configure("offline", foreground=C["red"])

    def _start_scan(self):
        """Start network scan."""
        if self._scanning:
            return

        self._scanning = True
        self._scan_status.config(text="Scanning...", fg=C["yellow"])
        self._tree.delete(*self._tree.get_children())

        def progress_callback(current):
            self._scan_status.config(text=f"Scanning... {current}/254", fg=C["yellow"])

        def result_callback(found):
            self._found_devices = found
            self._scanning = False
            self._scan_status.config(text=f"Found {len(found)} devices", fg=C["green"])
            self._populate_list(found)

        # Run scan in background thread
        thread = threading.Thread(
            target=lambda: NetworkScanner.scan(progress_callback, result_callback),
            daemon=True
        )
        thread.start()

    def _populate_list(self, devices):
        """Populate device list."""
        for ip, response_time in devices:
            # Verify if it's a Bitaxe
            is_bitaxe = NetworkScanner.verify_bitaxe(ip) is not None
            status = "✓ Bitaxe" if is_bitaxe else "✓ Responsive"
            tag = "good" if response_time < 50 else ("slow" if response_time < 200 else "offline")

            action_btn_id = f"btn_{ip.replace('.', '_')}"

            self._tree.insert("", "end", values=(
                ip,
                f"{response_time:.1f}",
                status,
                "Connect"
            ), tags=(tag,))

    def _refresh_list(self):
        """Refresh device list."""
        self._populate_list(self._found_devices)

    def on_tree_click(self, event):
        """Handle tree click."""
        item = self._tree.identify_row(event.y)
        if item:
            values = self._tree.item(item, "values")
            ip = values[0]
            if self._on_select:
                self._on_select(ip)
                self.destroy()


# ---------------------------------------------------------------------------
# Benchmark engine (runs in a background thread)
# ---------------------------------------------------------------------------

class BitaxeBenchmark:
    def __init__(self, config: dict, log_queue: queue.Queue):
        self.cfg         = config
        self.q           = log_queue
        self.stop_event  = threading.Event()

        self.bitaxe_url        = f"http://{config['ip']}"
        self.profile           = None
        self.small_core_count  = None
        self.asic_count        = None
        self.default_voltage   = None
        self.default_frequency = None
        self.results           = list(config.get("resume_results", []))
        self.start_time        = datetime.now().strftime("%Y-%m-%d_%H-%M")

        # v1.7: configurable from GUI
        self.err_max_valid   = config.get("err_max_valid", ERR_MAX_VALID)
        self.early_stop_n    = config.get("early_stop_steps", 3)
        self.adaptive_warmup = config.get("adaptive_warmup", True)
        self.autosave_interval = config.get("autosave_interval", 10)

        # v1.7: progress tracking
        self._total_steps    = 0
        self._done_steps     = 0
        self._bench_start_ts = None

        # v1.7: global retry counter to prevent timeout accumulo
        self._global_retries = 0
        self._max_global_retries = 10

        # v1.7: safety auto-stop tracking
        self._temp_history: list[tuple[float, float]] = []  # (timestamp, temp)
        self._safety_violations = 0

    # ------------------------------------------------------------------ log
    def _log(self, msg: str, color: str = "white"):
        self.q.put(("log", msg, color))

    def _status(self, msg: str):
        self.q.put(("status", msg))

    # ------------------------------------------------------------ API calls
    def _get(self, endpoint: str, timeout: int = 10):
        """v1.7: global retry limit to prevent timeout accumulo"""
        if self._global_retries >= self._max_global_retries:
            self._log(f"Max global retries ({self._max_global_retries}) reached — aborting.", "red")
            return None

        for attempt in range(3):
            if self.stop_event.is_set():
                return None
            try:
                r = requests.get(f"{self.bitaxe_url}{endpoint}", timeout=timeout)
                r.raise_for_status()
                self._global_retries = 0  # reset on success
                return r.json()
            except requests.exceptions.Timeout:
                self._global_retries += 1
                self._log(f"Timeout {endpoint} (attempt {attempt+1}/3, global {self._global_retries})", "yellow")
            except requests.exceptions.ConnectionError:
                self._global_retries += 1
                self._log(f"Connection error {endpoint} (attempt {attempt+1}/3)", "red")
            except requests.exceptions.RequestException as e:
                self._log(f"Request error {endpoint}: {e}", "red")
                break
            time.sleep(5)
        return None

    def _patch_settings(self, voltage: int, frequency: int) -> bool:
        try:
            r = requests.patch(
                f"{self.bitaxe_url}/api/system",
                json={"coreVoltage": voltage, "frequency": frequency},
                timeout=10,
            )
            r.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            self._log(f"Error applying settings: {e}", "red")
            return False

    def _restart(self, wait: bool = True):
        try:
            requests.post(f"{self.bitaxe_url}/api/system/restart", timeout=10)
            if not wait:
                return
            if self.adaptive_warmup:
                self._adaptive_wait()
            else:
                self._log(f"Restarting — waiting {SLEEP_TIME}s for stabilisation…", "yellow")
                for i in range(SLEEP_TIME):
                    if self.stop_event.is_set():
                        return
                    time.sleep(1)
        except requests.exceptions.RequestException as e:
            self._log(f"Restart error: {e}", "red")

    def _adaptive_wait(self):
        """Wait for chip temperature to stabilise."""
        MIN_WAIT  = 20
        MAX_WAIT  = SLEEP_TIME * 2
        STABLE_DT = 1.0
        CHECK_INT = 10

        self._log("Adaptive warm-up: waiting for boot…", "yellow")
        elapsed = 0
        for _ in range(MIN_WAIT):
            if self.stop_event.is_set():
                return
            time.sleep(1)
        elapsed += MIN_WAIT

        prev_temp = None
        while elapsed < MAX_WAIT:
            if self.stop_event.is_set():
                return
            info = self._get("/api/system/info")
            cur_temp = self._get_max_temp(info) if info else None
            if cur_temp is not None:
                if prev_temp is not None and abs(cur_temp - prev_temp) <= STABLE_DT:
                    self._log(
                        f"Adaptive warm-up: stable at {cur_temp:.1f}°C "
                        f"after {elapsed}s ✓", "green"
                    )
                    return
                prev_temp = cur_temp
                self._log(
                    f"Adaptive warm-up: {cur_temp:.1f}°C — waiting…",
                    "yellow"
                )
            for _ in range(CHECK_INT):
                if self.stop_event.is_set():
                    return
                time.sleep(1)
            elapsed += CHECK_INT

        self._log(f"Adaptive warm-up: timeout after {elapsed}s — continuing.", "yellow")

    def _set_and_restart(self, voltage: int, frequency: int, wait: bool = True):
        self._log(f"  → {voltage}mV / {frequency}MHz", "yellow")
        if self._patch_settings(voltage, frequency):
            self._restart(wait=wait)

    # --------------------------------------------------- model detection
    def _all_string_values(self, d: dict) -> list[str]:
        out = []
        for v in d.values():
            if isinstance(v, str):
                out.append(v.lower())
            elif isinstance(v, dict):
                out.extend(self._all_string_values(v))
        return out

    def _detect_profile(self, system_info: dict, current_hashrate_ghs: float | None) -> dict:
        chip_mode = self.cfg["chip_mode"]
        if chip_mode == "single":
            self._log("Profile: forced SINGLE-chip by user.", "green")
            return self._make_profile("single")
        if chip_mode == "dual":
            self._log("Profile: forced DUAL-chip by user.", "green")
            return self._make_profile("dual")

        api_asic = system_info.get("asicCount")
        if api_asic is not None and int(api_asic) >= 2:
            self._log(f"Auto-detect: asicCount={api_asic} → DUAL-chip.", "green")
            return self._make_profile("dual")

        all_strings = self._all_string_values(system_info)
        for kw in DUAL_CHIP_KEYWORDS:
            for s in all_strings:
                if kw in s:
                    self._log(f"Auto-detect: found keyword '{kw}' → DUAL-chip.", "green")
                    return self._make_profile("dual")

        if current_hashrate_ghs and current_hashrate_ghs > DUAL_CHIP_HASHRATE_THRESHOLD_GHS:
            self._log(
                f"Auto-detect: live hashrate {current_hashrate_ghs:.0f} GH/s "
                f"> {DUAL_CHIP_HASHRATE_THRESHOLD_GHS} GH/s → DUAL-chip.", "green",
            )
            return self._make_profile("dual")

        self._log("Auto-detect: no dual-chip signal found → SINGLE-chip.", "green")
        return self._make_profile("single")

    def _make_profile(self, kind: str) -> dict:
        max_psu = self.cfg["max_psu_watts"]
        if kind == "dual":
            return {
                "kind":              "dual",
                "label":             "Dual-chip (GT 800/801, Duo 650 — 12V XT30)",
                "min_input_voltage": DUAL_CHIP_VMIN,
                "max_input_voltage": DUAL_CHIP_VMAX,
                "max_power":         max_psu,
                "max_temp":          self.cfg["max_temp"],
                "max_vr_temp":       self.cfg["max_vr_temp"],
            }
        return {
            "kind":              "single",
            "label":             "Single-chip (Gamma/Supra/Ultra — 5V barrel jack)",
            "min_input_voltage": SINGLE_CHIP_VMIN,
            "max_input_voltage": SINGLE_CHIP_VMAX,
            "max_power":         max_psu,
            "max_temp":          self.cfg["max_temp"],
            "max_vr_temp":       self.cfg["max_vr_temp"],
        }

    # ------------------------------------------------ fetch initial state
    def _fetch_settings(self) -> bool:
        self._status("Connecting to Bitaxe…")
        info = self._get("/api/system/info")
        if info is None:
            self._log("Cannot reach Bitaxe. Check IP and WiFi.", "red")
            return False

        if "smallCoreCount" not in info:
            self._log("Error: smallCoreCount missing from API. Cannot continue.", "red")
            return False

        self.small_core_count = info["smallCoreCount"]
        live_hr = info.get("hashRate")
        self.profile = self._detect_profile(info, live_hr)

        has_v  = "coreVoltage" in info
        has_f  = "frequency"   in info
        has_ac = "asicCount"   in info

        if has_v and has_f and has_ac:
            self.default_voltage   = info["coreVoltage"]
            self.default_frequency = info["frequency"]
            self.asic_count        = info["asicCount"]
        else:
            self._log("Fetching remaining info from /api/system/asic…", "yellow")
            asic = self._get("/api/system/asic")
            if asic is None:
                self._log("Cannot fetch /api/system/asic. Cannot continue.", "red")
                return False
            self.default_voltage   = asic.get("defaultVoltage",   1150)
            self.default_frequency = asic.get("defaultFrequency", 500)
            self.asic_count        = asic.get("asicCount",        1)

        if self.profile["kind"] == "dual" and (not self.asic_count or self.asic_count < 2):
            self._log(
                f"WARNING: API reports asicCount={self.asic_count} but profile is dual-chip. "
                "Forcing asicCount=2.", "yellow",
            )
            self.asic_count = 2

        total_cores = self.small_core_count * self.asic_count
        self._log("─" * 54, "white")
        self._log(f"Profile      : {self.profile['label']}", "green")
        self._log(f"ASIC count   : {self.asic_count}  (total cores: {total_cores})", "green")
        self._log(f"Default      : {self.default_voltage}mV / {self.default_frequency}MHz", "green")
        self._log(f"Input voltage: {self.profile['min_input_voltage']}–{self.profile['max_input_voltage']} mV", "green")
        self._log(f"Max PSU      : {self.profile['max_power']} W", "green")
        self._log(f"Max chip temp: {self.profile['max_temp']} °C", "green")
        self._log(f"Max VR temp  : {self.profile['max_vr_temp']} °C", "green")
        self._log("─" * 54, "white")
        return True

    # ----------------------------------------------------- temp helpers
    def _get_max_temp(self, info: dict):
        temps = [info.get("temp"), info.get("temp2")]
        valid = [t for t in temps if t is not None]
        return max(valid) if valid else None

    def _get_max_vr_temp(self, info: dict):
        vrs   = [info.get("vrTemp"), info.get("vrTemp2")]
        valid = [t for t in vrs if t is not None and t > 0]
        return max(valid) if valid else None

    # v1.7: Safety auto-stop thermal monitoring
    def _check_thermal_safety(self, current_temp: float) -> bool:
        """
        Returns True if safe to continue, False if thermal violation detected.
        Tracks temperature history and detects rapid rises.
        """
        now = time.time()
        self._temp_history.append((now, current_temp))

        # keep only last SAFETY_TEMP_WINDOW_SECS worth of data
        cutoff = now - SAFETY_TEMP_WINDOW_SECS
        self._temp_history = [(t, temp) for t, temp in self._temp_history if t >= cutoff]

        if len(self._temp_history) < 2:
            return True

        oldest_temp = self._temp_history[0][1]
        temp_delta = current_temp - oldest_temp

        if temp_delta > SAFETY_TEMP_RISE_THRESHOLD:
            self._safety_violations += 1
            self._log(
                f"⚠ THERMAL SAFETY: temp rose {temp_delta:.1f}°C in {SAFETY_TEMP_WINDOW_SECS}s "
                f"(violation {self._safety_violations}/{SAFETY_MAX_VIOLATIONS})", "red"
            )

            if self._safety_violations >= SAFETY_MAX_VIOLATIONS:
                self._log("⛔ THERMAL SAFETY: max violations reached — ABORTING benchmark!", "red")
                return False

            # pause and retry
            self._log(f"Pausing 2 minutes to cool down…", "yellow")
            for _ in range(120):
                if self.stop_event.is_set():
                    return False
                time.sleep(1)

            self._temp_history.clear()  # reset after cooldown
            return True

        return True

    # ------------------------------------------------- error-rate helpers
    def _get_error_percentage(self, info: dict) -> float | None:
        for field in ("errorPercentage", "asicErrorRate"):
            val = info.get(field)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return None

    def _get_asic_error_counts(self, info: dict) -> list[int] | None:
        monitor = info.get("hashrateMonitor")
        if not isinstance(monitor, dict):
            return None
        asics = monitor.get("asics")
        if not isinstance(asics, list) or not asics:
            return None
        counts = []
        for chip in asics:
            ec = chip.get("errorCount")
            if ec is not None:
                try:
                    counts.append(int(ec))
                except (TypeError, ValueError):
                    pass
        return counts if counts else None

    # -------------------------------------------------- benchmark loop
    def _benchmark_iteration(self, voltage: int, frequency: int):
        """
        Returns (avg_hashrate, avg_temp, efficiency_jth, hashrate_ok,
                 avg_vr_temp, avg_error_rate, error_reason)
        """
        p           = self.profile
        expected_hr = frequency * (self.small_core_count * self.asic_count / 1000)

        hash_rates, temperatures, powers, vr_temps_list, error_rates = [], [], [], [], []
        total_samples = BENCHMARK_TIME // SAMPLE_INTERVAL

        self._status(f"Testing {voltage}mV / {frequency}MHz…")

        prev_error_counts: list[int] | None = None

        for sample in range(total_samples):
            if self.stop_event.is_set():
                return None, None, None, False, None, None, "STOPPED"

            info = self._get("/api/system/info")
            if info is None:
                return None, None, None, False, None, None, "SYSTEM_INFO_FAILURE"

            temp       = self._get_max_temp(info)
            vr_temp    = self._get_max_vr_temp(info)
            voltage_in = info.get("voltage")
            hash_rate  = info.get("hashRate")
            power      = info.get("power")

            # v1.7: thermal safety check
            if temp is not None:
                if not self._check_thermal_safety(temp):
                    return None, None, None, False, None, None, "THERMAL_SAFETY_ABORT"

            # error rate
            err_rate = self._get_error_percentage(info)
            cur_error_counts = self._get_asic_error_counts(info)
            if err_rate is None and cur_error_counts is not None and prev_error_counts is not None:
                if len(cur_error_counts) == len(prev_error_counts):
                    deltas = [
                        max(0, cur - prev)
                        for cur, prev in zip(cur_error_counts, prev_error_counts)
                    ]
                    total_delta = sum(deltas)
                    expected_hashes = expected_hr * 1e9 * SAMPLE_INTERVAL
                    if expected_hashes > 0:
                        err_rate = (total_delta / expected_hashes) * 100.0

            prev_error_counts = cur_error_counts

            # safety checks
            if temp is None:
                return None, None, None, False, None, None, "TEMPERATURE_DATA_FAILURE"
            if temp < 5:
                return None, None, None, False, None, None, "TEMPERATURE_BELOW_5"
            if temp >= p["max_temp"]:
                self._log(f"⚠ Chip temp {temp:.0f}°C ≥ {p['max_temp']}°C — stopping.", "red")
                return None, None, None, False, None, None, "CHIP_TEMP_EXCEEDED"
            if vr_temp is not None and vr_temp >= p["max_vr_temp"]:
                self._log(f"⚠ VR temp {vr_temp:.0f}°C ≥ {p['max_vr_temp']}°C — stopping.", "red")
                return None, None, None, False, None, None, "VR_TEMP_EXCEEDED"
            if voltage_in is not None:
                if voltage_in < p["min_input_voltage"]:
                    self._log(
                        f"⚠ Input voltage {voltage_in} mV below {p['min_input_voltage']} mV — stopping.", "red"
                    )
                    return None, None, None, False, None, None, "INPUT_VOLTAGE_BELOW_MIN"
                if voltage_in > p["max_input_voltage"]:
                    self._log(
                        f"⚠ Input voltage {voltage_in} mV above {p['max_input_voltage']} mV — stopping.", "red"
                    )
                    return None, None, None, False, None, None, "INPUT_VOLTAGE_ABOVE_MAX"
            if hash_rate is None or power is None:
                return None, None, None, False, None, None, "HASHRATE_POWER_DATA_FAILURE"
            if power > p["max_power"]:
                self._log(f"⚠ Power {power:.1f}W > {p['max_power']}W PSU limit — stopping.", "red")
                return None, None, None, False, None, None, "POWER_EXCEEDED"

            hash_rates.append(hash_rate)
            temperatures.append(temp)
            powers.append(power)
            if vr_temp is not None:
                vr_temps_list.append(vr_temp)
            if err_rate is not None and sample > 0:
                error_rates.append(err_rate)

            pct  = (sample + 1) / total_samples * 100
            line = (
                f"[{sample+1:2d}/{total_samples}] {pct:5.1f}% | "
                f"{voltage}mV {frequency}MHz | "
                f"HR: {hash_rate:.0f} GH/s | "
                f"T: {temp:.0f}°C"
            )
            if vr_temp is not None:
                line += f" VR: {vr_temp:.0f}°C"
            line += f" | {power:.1f}W"
            if err_rate is not None and sample > 0:
                err_color = "red" if err_rate > ERR_MAX_VALID else ("yellow" if err_rate > ERR_OPT_HIGH else "green")
                self._log(line, "white")
                self._log(f"         Err: {err_rate:.3f}%", err_color)
            else:
                self._log(line, "white")

            if sample < total_samples - 1:
                time.sleep(SAMPLE_INTERVAL)

        if not hash_rates:
            return None, None, None, False, None, None, "NO_DATA_COLLECTED"

        # trim outliers
        s_hr    = sorted(hash_rates)
        trim_hr = s_hr[3:-3] if len(s_hr) > 6 else s_hr
        avg_hr  = sum(trim_hr) / len(trim_hr)

        s_t     = sorted(temperatures)
        trim_t  = s_t[6:] if len(s_t) > 6 else s_t
        avg_temp = sum(trim_t) / len(trim_t)

        avg_vr = None
        if vr_temps_list:
            s_vr   = sorted(vr_temps_list)
            trim_v = s_vr[6:] if len(s_vr) > 6 else s_vr
            avg_vr = sum(trim_v) / len(trim_v)

        avg_pwr = sum(powers) / len(powers)
        avg_err = (sum(error_rates) / len(error_rates)) if error_rates else None

        # v1.7: Fix A — division by zero protection
        if avg_hr <= 0:
            return None, None, None, False, None, avg_err, "ZERO_HASHRATE"

        eff_jth = avg_pwr / (avg_hr / 1000) if avg_hr > 0 else 999.99
        hr_ok   = avg_hr >= expected_hr * 0.94

        self._log(f"  Avg HR   : {avg_hr:.1f} GH/s  (expected ≥ {expected_hr*0.94:.1f})", "green")
        self._log(f"  Avg temp : {avg_temp:.1f}°C{'  VR: '+f'{avg_vr:.1f}°C' if avg_vr else ''}", "green")
        self._log(f"  Eff      : {eff_jth:.2f} J/TH  |  Power: {avg_pwr:.1f}W", "green")
        if avg_err is not None:
            err_color = "red" if avg_err > ERR_MAX_VALID else ("yellow" if avg_err > ERR_OPT_HIGH else "green")
            self._log(f"  Avg Err  : {avg_err:.3f}%", err_color)

        return avg_hr, avg_temp, eff_jth, hr_ok, avg_vr, avg_err, None

    # v1.7: Auto-save partial results
    def _autosave(self):
        """Save current results to a temporary JSON file."""
        ip = self.cfg["ip"].replace(".", "_")
        filename = f"bitaxe_benchmark_{ip}_{self.start_time}_PARTIAL.json"
        try:
            data = {
                "profile":     self.profile["label"],
                "sweep":       "2D voltage × frequency (PARTIAL)",
                "all_results": self.results,
                "timestamp":   datetime.now().isoformat(),
            }
            with open(filename, "w") as f:
                json.dump(data, f, indent=4)
            self._log(f"Auto-saved → {filename} ({len(self.results)} steps)", "yellow")
        except IOError as e:
            self._log(f"Auto-save error: {e}", "red")

    # ---------------------------------------------------- save / reset
    def _save(self):
        ip       = self.cfg["ip"].replace(".", "_")
        filename = f"bitaxe_benchmark_{ip}_{self.start_time}.json"
        try:
            stable   = [r for r in self.results if r.get("stable", True)]
            pool     = stable if stable else self.results
            top5_hr  = sorted(pool, key=lambda x: x["averageHashRate"], reverse=True)[:5]
            top5_eff = sorted(pool, key=lambda x: x["efficiencyJTH"])[:5]
            data = {
                "profile":        self.profile["label"],
                "sweep":          "2D voltage × frequency",
                "all_results":    self.results,
                "top_performers": top5_hr,
                "most_efficient": top5_eff,
            }
            with open(filename, "w") as f:
                json.dump(data, f, indent=4)
            self._log(f"Results saved → {filename}", "green")
        except IOError as e:
            self._log(f"Error saving: {e}", "red")

    def _save_csv(self):
        ip       = self.cfg["ip"].replace(".", "_")
        filename = f"bitaxe_benchmark_{ip}_{self.start_time}.csv"
        fieldnames = ["coreVoltage", "frequency", "averageHashRate",
                      "averageTemperature", "efficiencyJTH", "stable",
                      "averageVRTemp", "averageErrorRate", "profile"]
        try:
            with open(filename, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(self.results)
            self._log(f"CSV saved → {filename}", "green")
        except IOError as e:
            self._log(f"Error saving CSV: {e}", "red")

    # v1.7: Export Markdown report
    def _save_markdown(self):
        """Generate a GitHub-flavored Markdown report."""
        ip       = self.cfg["ip"].replace(".", "_")
        filename = f"bitaxe_benchmark_{ip}_{self.start_time}.md"

        stable = [r for r in self.results if r.get("stable", True)]
        pool   = stable if stable else self.results

        if not pool:
            self._log("No results to export to Markdown.", "yellow")
            return

        best_hr  = max(pool, key=lambda x: x["averageHashRate"])
        best_eff = min(pool, key=lambda x: x["efficiencyJTH"])

        md = f"""# Bitaxe Benchmark Report

**Profile:** {self.profile['label']}
**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Total steps:** {len(self.results)} ({len(stable)} stable)

---

## 🏆 Best Configurations

### Highest Hashrate
- **Voltage:** {best_hr['coreVoltage']} mV
- **Frequency:** {best_hr['frequency']} MHz
- **Hashrate:** {best_hr['averageHashRate']:.1f} GH/s
- **Efficiency:** {best_hr['efficiencyJTH']:.2f} J/TH
- **Temperature:** {best_hr['averageTemperature']:.1f}°C

### Most Efficient
- **Voltage:** {best_eff['coreVoltage']} mV
- **Frequency:** {best_eff['frequency']} MHz
- **Hashrate:** {best_eff['averageHashRate']:.1f} GH/s
- **Efficiency:** {best_eff['efficiencyJTH']:.2f} J/TH
- **Temperature:** {best_eff['averageTemperature']:.1f}°C

---

## 📊 Top 5 by Hashrate

| Rank | Voltage (mV) | Freq (MHz) | Hashrate (GH/s) | Efficiency (J/TH) | Temp (°C) |
|------|--------------|------------|-----------------|-------------------|-----------|
"""
        top5_hr = sorted(pool, key=lambda x: x["averageHashRate"], reverse=True)[:5]
        for i, r in enumerate(top5_hr, 1):
            md += f"| {i} | {r['coreVoltage']} | {r['frequency']} | {r['averageHashRate']:.1f} | {r['efficiencyJTH']:.2f} | {r['averageTemperature']:.1f} |\n"

        md += "\n---\n\n## 📊 Top 5 by Efficiency\n\n"
        md += "| Rank | Voltage (mV) | Freq (MHz) | Hashrate (GH/s) | Efficiency (J/TH) | Temp (°C) |\n"
        md += "|------|--------------|------------|-----------------|-------------------|-----------||\n"

        top5_eff = sorted(pool, key=lambda x: x["efficiencyJTH"])[:5]
        for i, r in enumerate(top5_eff, 1):
            md += f"| {i} | {r['coreVoltage']} | {r['frequency']} | {r['averageHashRate']:.1f} | {r['efficiencyJTH']:.2f} | {r['averageTemperature']:.1f} |\n"

        md += f"\n---\n\n*Generated by BitaxeBenchGui v1.8*\n"

        try:
            with open(filename, "w") as f:
                f.write(md)
            self._log(f"Markdown report saved → {filename}", "green")
        except IOError as e:
            self._log(f"Error saving Markdown: {e}", "red")

    def _apply_best(self):
        if not self.results:
            self._log("No results — restoring device defaults.", "yellow")
            self._set_and_restart(self.default_voltage, self.default_frequency, wait=False)
            return
        stable = [r for r in self.results if r.get("stable", True)]
        pool   = stable if stable else self.results
        best   = sorted(pool, key=lambda x: x["averageHashRate"], reverse=True)[0]
        self._log(
            f"Best: {best['coreVoltage']}mV / {best['frequency']}MHz "
            f"→ {best['averageHashRate']:.1f} GH/s"
            f"{'  ✓ stable' if best.get('stable') else '  ⚠ unstable'}", "green"
        )
        self._set_and_restart(best["coreVoltage"], best["frequency"], wait=False)

    def _print_summary(self):
        if not self.results:
            return
        stable = [r for r in self.results if r.get("stable", True)]
        pool   = stable if stable else self.results
        top5   = sorted(pool, key=lambda x: x["averageHashRate"], reverse=True)[:5]
        self._log("─" * 54, "white")
        self._log(
            f"TOP 5 STABLE CONFIGURATIONS BY HASHRATE "
            f"({len(stable)}/{len(self.results)} steps stable)", "green"
        )
        for i, r in enumerate(top5, 1):
            line = (
                f"  #{i}  {r['coreVoltage']}mV / {r['frequency']}MHz → "
                f"{r['averageHashRate']:.1f} GH/s  {r['efficiencyJTH']:.2f} J/TH"
                f"  {r['averageTemperature']:.1f}°C"
            )
            if "averageVRTemp" in r:
                line += f"  VR {r['averageVRTemp']:.1f}°C"
            if "averageErrorRate" in r and r["averageErrorRate"] is not None:
                line += f"  Err {r['averageErrorRate']:.3f}%"
            self._log(line, "green")

    # ----------------------------------------------------------- run
    def run(self):
        try:
            if not self._fetch_settings():
                self.q.put(("done", "error"))
                return

            self._log("DISCLAIMER: overclocking may damage hardware. Use at your own risk.", "red")

            start_v    = self.cfg["voltage"]
            start_f    = self.cfg["frequency"]
            v_step     = self.cfg["voltage_increment"]
            f_step     = self.cfg["frequency_increment"]
            max_v      = self.cfg["max_voltage"]
            max_f      = self.cfg["max_frequency"]
            early_n    = self.early_stop_n

            done_pairs = {
                (r["coreVoltage"], r["frequency"])
                for r in self.results
            }

            v_levels = list(range(start_v, max_v + 1, v_step))
            if v_levels[-1] < max_v:
                v_levels.append(max_v)
            f_levels = list(range(start_f, max_f + 1, f_step))
            if f_levels[-1] < max_f:
                f_levels.append(max_f)
            self._total_steps = len(v_levels) * len(f_levels)
            self._done_steps  = len(done_pairs)
            self._bench_start_ts = time.monotonic()

            def _push_progress():
                if self._total_steps == 0:
                    return
                pct = min(100.0, self._done_steps / self._total_steps * 100)
                elapsed = time.monotonic() - self._bench_start_ts
                if self._done_steps > 0:
                    secs_per_step = elapsed / self._done_steps
                    remaining     = secs_per_step * (self._total_steps - self._done_steps)
                    eta = str(timedelta(seconds=int(remaining)))
                    eta_str = f"ETA {eta}  ({self._done_steps}/{self._total_steps})"
                else:
                    eta_str = f"0/{self._total_steps} steps"
                self.q.put(("progress", pct, eta_str))

            _push_progress()

            cur_v = start_v

            while cur_v <= max_v:
                if self.stop_event.is_set():
                    break

                self._log("─" * 54, "white")
                self._log(f"▶ Voltage level: {cur_v} mV", "yellow")

                cur_f          = start_f
                decline_streak = 0
                prev_hr        = None

                while cur_f <= max_f:
                    if self.stop_event.is_set():
                        break

                    if (cur_v, cur_f) in done_pairs:
                        self._log(
                            f"  Resume: skipping {cur_v}mV / {cur_f}MHz (already done).",
                            "white"
                        )
                        cur_f += f_step
                        self._done_steps += 1
                        _push_progress()
                        continue

                    self._set_and_restart(cur_v, cur_f)
                    if self.stop_event.is_set():
                        break

                    avg_hr, avg_t, eff, ok, avg_vr, avg_err, err = \
                        self._benchmark_iteration(cur_v, cur_f)

                    if self.stop_event.is_set():
                        break

                    self._done_steps += 1
                    _push_progress()

                    # v1.7: auto-save every N steps
                    if self.autosave_interval > 0 and self._done_steps % self.autosave_interval == 0:
                        self._autosave()

                    if avg_hr is not None:
                        self.q.put(("chart", avg_hr))

                        result = {
                            "coreVoltage":        cur_v,
                            "frequency":          cur_f,
                            "averageHashRate":    avg_hr,
                            "averageTemperature": avg_t,
                            "efficiencyJTH":      eff,
                            "profile":            self.profile["label"],
                            "stable":             ok,
                        }
                        if avg_vr is not None:
                            result["averageVRTemp"] = avg_vr
                        if avg_err is not None:
                            result["averageErrorRate"] = avg_err
                        self.results.append(result)

                        if not ok:
                            self._log(
                                f"  Hashrate low at {cur_v}mV / {cur_f}MHz — "
                                "recorded, continuing.", "yellow"
                            )

                        # early-stop
                        if early_n > 0:
                            if prev_hr is not None and avg_hr < prev_hr:
                                decline_streak += 1
                                if decline_streak >= early_n:
                                    self._log(
                                        f"  Early-stop: HR declined for {early_n} consecutive "
                                        f"steps — moving to next voltage.", "yellow"
                                    )
                                    break
                            else:
                                decline_streak = 0
                        prev_hr = avg_hr

                        cur_f += f_step

                    else:
                        self._log(
                            f"  Safety limit ({err}) at {cur_v}mV / {cur_f}MHz — "
                            "skipping remaining frequencies.", "red"
                        )
                        break

                if self.stop_event.is_set():
                    break

                next_v = cur_v + v_step
                if next_v > max_v and cur_v < max_v:
                    cur_v = max_v
                else:
                    cur_v = next_v

            if not self.stop_event.is_set():
                self._log("─" * 54, "white")
                self._log("Sweep complete — all combinations tested.", "green")

        except Exception as e:
            self._log(f"Unexpected error: {e}", "red")

        finally:
            self._apply_best()
            if self.results:
                self._save()
                self._save_csv()
                self._save_markdown()  # v1.7
                self._print_summary()
            self._status("Benchmark finished.")
            self.q.put(("progress", 100, "Done!"))
            self.q.put(("done", "ok"))


# ---------------------------------------------------------------------------
# v1.7: Comparison window (side-by-side analysis)
# ---------------------------------------------------------------------------

class ComparisonWindow(tk.Toplevel):
    """Load and compare two benchmark JSONs side-by-side."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("📊 Benchmark Comparison")
        self.configure(bg=C["bg"])
        self.resizable(True, True)
        self.minsize(1000, 600)

        self._data_a = None
        self._data_b = None

        self._build_ui()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=C["accent"], height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="⚖  BENCHMARK COMPARISON",
            bg=C["accent"], fg="#111827",
            font=("Courier", 14, "bold"),
        ).pack(side="left", padx=16, pady=10)

        # Load buttons
        btn_frame = tk.Frame(self, bg=C["bg"])
        btn_frame.pack(fill="x", padx=14, pady=10)

        ttk.Button(btn_frame, text="📂 Load Benchmark A",
                   command=lambda: self._load_file('a')).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="📂 Load Benchmark B",
                   command=lambda: self._load_file('b')).pack(side="left")

        # Split frame
        split = tk.Frame(self, bg=C["bg"])
        split.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        split.columnconfigure(0, weight=1)
        split.columnconfigure(1, weight=1)

        # Panel A
        panel_a = ttk.LabelFrame(split, text="  Benchmark A", padding=10)
        panel_a.grid(row=0, column=0, sticky="nsew", padx=(0, 7))

        self._text_a = scrolledtext.ScrolledText(
            panel_a, height=25, width=50,
            font=("Courier", 9), bg=C["log_bg"], fg=C["text"],
            state="disabled", wrap="word"
        )
        self._text_a.pack(fill="both", expand=True)

        # Panel B
        panel_b = ttk.LabelFrame(split, text="  Benchmark B", padding=10)
        panel_b.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        self._text_b = scrolledtext.ScrolledText(
            panel_b, height=25, width=50,
            font=("Courier", 9), bg=C["log_bg"], fg=C["text"],
            state="disabled", wrap="word"
        )
        self._text_b.pack(fill="both", expand=True)

        split.rowconfigure(0, weight=1)

    def _load_file(self, slot: str):
        path = filedialog.askopenfilename(
            title=f"Select benchmark JSON for slot {slot.upper()}",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot read file:\n{e}", parent=self)
            return

        if slot == 'a':
            self._data_a = data
            self._populate(self._text_a, data, Path(path).name)
        else:
            self._data_b = data
            self._populate(self._text_b, data, Path(path).name)

    def _populate(self, widget, data: dict, filename: str):
        widget.config(state="normal")
        widget.delete("1.0", "end")

        results = data.get("all_results", [])
        profile = data.get("profile", "unknown")

        if not results:
            widget.insert("end", "No results in this file.")
            widget.config(state="disabled")
            return

        stable = [r for r in results if r.get("stable", True)]
        pool = stable if stable else results

        best_hr = max(pool, key=lambda x: x["averageHashRate"])
        best_eff = min(pool, key=lambda x: x["efficiencyJTH"])

        summary = f"""File: {filename}
Profile: {profile}
Total steps: {len(results)} ({len(stable)} stable)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏆 BEST HASHRATE
  {best_hr['coreVoltage']} mV / {best_hr['frequency']} MHz
  → {best_hr['averageHashRate']:.1f} GH/s
  {best_hr['efficiencyJTH']:.2f} J/TH
  {best_hr['averageTemperature']:.1f}°C

⚡ MOST EFFICIENT
  {best_eff['coreVoltage']} mV / {best_eff['frequency']} MHz
  → {best_eff['averageHashRate']:.1f} GH/s
  {best_eff['efficiencyJTH']:.2f} J/TH
  {best_eff['averageTemperature']:.1f}°C

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TOP 5 BY HASHRATE:
"""

        top5_hr = sorted(pool, key=lambda x: x["averageHashRate"], reverse=True)[:5]
        for i, r in enumerate(top5_hr, 1):
            summary += f"\n{i}. {r['coreVoltage']}mV {r['frequency']}MHz → {r['averageHashRate']:.1f} GH/s ({r['efficiencyJTH']:.2f} J/TH)"

        summary += "\n\nTOP 5 BY EFFICIENCY:\n"
        top5_eff = sorted(pool, key=lambda x: x["efficiencyJTH"])[:5]
        for i, r in enumerate(top5_eff, 1):
            summary += f"\n{i}. {r['coreVoltage']}mV {r['frequency']}MHz → {r['efficiencyJTH']:.2f} J/TH ({r['averageHashRate']:.1f} GH/s)"

        widget.insert("end", summary)
        widget.config(state="disabled")


# ---------------------------------------------------------------------------
# Analysis window (original with v1.7 enhancements)
# ---------------------------------------------------------------------------

class AnalysisWindow(tk.Toplevel):
    """v1.7: Added heatmap click-to-details."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("📊 Benchmark Analysis v1.8")
        self.configure(bg=C["bg"])
        self.resizable(True, True)
        self.minsize(860, 580)
        self._results  = []
        self._profile  = ""
        self._hmap_mode = tk.StringVar(value="hashrate")
        self._build_ui()
        self._load_file()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=C["accent"], height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="⛏  BENCHMARK ANALYSIS",
            bg=C["accent"], fg="#111827",
            font=("Courier", 14, "bold"),
        ).pack(side="left", padx=16, pady=10)

        # Best-step card
        self._card_frame = tk.Frame(self, bg=C["panel"], pady=10, padx=14)
        self._card_frame.pack(fill="x", padx=14, pady=(10, 4))
        self._card_label = tk.Label(
            self._card_frame,
            text="Load a JSON file to see results.",
            bg=C["panel"], fg=C["text"],
            font=("Courier", 10), justify="left", anchor="w",
        )
        self._card_label.pack(fill="x")

        # Notebook
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=14, pady=(4, 0))

        # Tab 1: Table
        tab_table = tk.Frame(nb, bg=C["bg"])
        nb.add(tab_table, text="  📋  Results Table  ")

        tbl_outer = tk.Frame(tab_table, bg=C["bg"])
        tbl_outer.pack(fill="both", expand=True, pady=4)

        cols = ("Volt (mV)", "Freq (MHz)", "HR (GH/s)", "Power (W)",
                "J/TH", "Temp (°C)", "VR Temp", "Err %", "Status")
        self._tree = ttk.Treeview(tbl_outer, columns=cols, show="headings", height=16)
        col_widths = [80, 85, 90, 75, 70, 75, 70, 70, 100]
        for col, w in zip(cols, col_widths):
            self._tree.heading(col, text=col)
            self._tree.column(col, width=w, anchor="center")

        sb = ttk.Scrollbar(tbl_outer, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._tree.pack(fill="both", expand=True)

        self._tree.tag_configure("optimal",    background="#14532d", foreground=C["text"])
        self._tree.tag_configure("acceptable", background="#422006", foreground=C["text"])
        self._tree.tag_configure("discarded",  background="#450a0a", foreground="#9ca3af")
        self._tree.tag_configure("nodata",     background="#1e293b", foreground=C["muted"])
        self._tree.tag_configure("best",       background="#854d0e", foreground="#fef08a")

        # Legend
        legend = tk.Frame(tab_table, bg=C["bg"])
        legend.pack(fill="x", pady=(4, 8))
        for bg, label in [
            ("#14532d", "Optimal (0.20–0.70 %)"),
            ("#422006", "Acceptable (0.70–1.00 %)"),
            ("#450a0a", "Discarded (> 1.00 %)"),
            ("#854d0e", "★ Best step"),
            ("#1e293b", "No error data"),
        ]:
            tk.Label(legend, text="  ", bg=bg, width=2).pack(side="left")
            tk.Label(legend, text=f" {label}   ", bg=C["bg"], fg=C["muted"],
                     font=("Courier", 8)).pack(side="left")

        # Tab 2: Heatmap
        tab_heat = tk.Frame(nb, bg=C["bg"])
        nb.add(tab_heat, text="  🔥  Heatmap  ")

        ctrl = tk.Frame(tab_heat, bg=C["bg"])
        ctrl.pack(fill="x", padx=8, pady=6)
        tk.Label(ctrl, text="Colour by:", bg=C["bg"], fg=C["text"],
                 font=("Courier", 9)).pack(side="left")
        for txt, val in [("Hashrate (GH/s)", "hashrate"), ("Efficiency (J/TH)", "jth")]:
            ttk.Radiobutton(
                ctrl, text=txt, variable=self._hmap_mode, value=val,
                command=self._redraw_heatmap,
            ).pack(side="left", padx=8)

        self._hmap_canvas = tk.Canvas(tab_heat, bg=C["log_bg"],
                                       highlightthickness=0)
        self._hmap_canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._hmap_canvas.bind("<Configure>", lambda e: self._redraw_heatmap())

        # v1.7: click handler for heatmap details
        self._hmap_canvas.bind("<Button-1>", self._on_heatmap_click)

        # store cell coordinates for click detection
        self._hmap_cells: dict[tuple[int, int], dict] = {}

    def _load_file(self):
        path = filedialog.askopenfilename(
            title="Select benchmark JSON file",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            self.destroy()
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot read file:\n{e}", parent=self)
            self.destroy()
            return

        results = data.get("all_results", [])
        if not results:
            messagebox.showwarning("Empty", "No results found in the JSON file.", parent=self)
            self.destroy()
            return

        self._results = results
        self._profile = data.get("profile", "unknown")
        self._populate(results, self._profile)
        self.after(100, self._redraw_heatmap)

    def _populate(self, results: list, profile: str):
        for row in self._tree.get_children():
            self._tree.delete(row)

        valid_steps   = []
        optimal_steps = []

        for r in results:
            err = r.get("averageErrorRate")
            if err is None:
                tag = "nodata"
            elif err > ERR_MAX_VALID:
                tag = "discarded"
            elif ERR_OPT_LOW <= err <= ERR_OPT_HIGH:
                tag = "optimal"
                valid_steps.append(r)
                optimal_steps.append(r)
            else:
                tag = "acceptable"
                valid_steps.append(r)
            r["_tag"] = tag

        pool = optimal_steps if optimal_steps else valid_steps
        best = None
        if pool:
            best = min(pool, key=lambda x: x["efficiencyJTH"])

        if best is None:
            no_err = [r for r in results if r.get("averageErrorRate") is None]
            if no_err:
                best = min(no_err, key=lambda x: x["efficiencyJTH"])

        for r in results:
            err     = r.get("averageErrorRate")
            vr_str  = f"{r['averageVRTemp']:.1f}" if "averageVRTemp" in r else "—"
            err_str = f"{err:.3f}" if err is not None else "—"
            tag     = r["_tag"]

            is_best = (best is not None and
                       r["coreVoltage"] == best["coreVoltage"] and
                       r["frequency"]   == best["frequency"])
            if is_best:
                tag = "best"

            self._tree.insert("", "end", values=(
                r["coreVoltage"],
                r["frequency"],
                f"{r['averageHashRate']:.1f}",
                f"{r['averageHashRate'] * r['efficiencyJTH'] / 1000:.1f}",
                f"{r['efficiencyJTH']:.2f}",
                f"{r['averageTemperature']:.1f}",
                vr_str,
                err_str,
                "★ BEST" if is_best else tag.upper().replace("_", " "),
            ), tags=(tag,))

        if best:
            err_b   = best.get("averageErrorRate")
            err_txt = f"{err_b:.3f} %" if err_b is not None else "no data"
            power_b = best["averageHashRate"] * best["efficiencyJTH"] / 1000
            win_msg = (
                f"  ★  BEST CONFIGURATION  ★\n\n"
                f"  Profile   : {profile}\n"
                f"  Voltage   : {best['coreVoltage']} mV\n"
                f"  Frequency : {best['frequency']} MHz\n"
                f"  Hashrate  : {best['averageHashRate']:.1f} GH/s\n"
                f"  Power     : {power_b:.1f} W\n"
                f"  Efficiency: {best['efficiencyJTH']:.2f} J/TH\n"
                f"  Chip temp : {best['averageTemperature']:.1f} °C"
            )
            if "averageVRTemp" in best:
                win_msg += f"\n  VR temp   : {best['averageVRTemp']:.1f} °C"
            win_msg += f"\n  Error rate: {err_txt}"
            pool_name = ("optimal window (0.20–0.70 %)" if optimal_steps else
                         ("valid steps (≤ 1.00 %)" if valid_steps else
                          "all steps (no error data)"))
            win_msg += f"\n\n  Selected from: {pool_name}"
            self._card_label.config(text=win_msg, fg=C["yellow"])
        else:
            self._card_label.config(
                text="  No valid steps found (all steps have error rate > 1 %).",
                fg=C["red"])

    def _redraw_heatmap(self):
        """Draw interactive voltage × frequency heatmap."""
        c      = self._hmap_canvas
        c.delete("all")
        data   = self._results
        if not data:
            return

        mode   = self._hmap_mode.get()
        W      = c.winfo_width()  or 700
        H      = c.winfo_height() or 400

        voltages = sorted(set(r["coreVoltage"] for r in data))
        freqs    = sorted(set(r["frequency"]   for r in data))
        nv, nf   = len(voltages), len(freqs)
        if nv == 0 or nf == 0:
            return

        lookup = {(r["coreVoltage"], r["frequency"]): r for r in data}

        vals = []
        for r in data:
            v = r["averageHashRate"] if mode == "hashrate" else r["efficiencyJTH"]
            vals.append(v)
        lo, hi = min(vals), max(vals)
        span   = hi - lo if hi != lo else 1.0

        ML, MR, MT, MB = 60, 20, 30, 50
        cell_w = (W - ML - MR) / nf
        cell_h = (H - MT - MB) / nv

        def heat_color(val):
            t = (val - lo) / span
            if mode == "jth":
                t = 1 - t
            r_c = int(min(255, t * 2 * 255))
            g_c = int(min(255, (1 - abs(t - 0.5) * 2) * 255))
            b_c = int(max(0, (1 - t * 2) * 255))
            return f"#{r_c:02x}{g_c:02x}{b_c:02x}"

        # v1.7: store cell coords for click detection
        self._hmap_cells.clear()

        for vi, volt in enumerate(voltages):
            for fi, freq in enumerate(freqs):
                rec = lookup.get((volt, freq))
                x0  = ML + fi * cell_w
                y0  = MT + vi * cell_h
                x1  = x0 + cell_w
                y1  = y0 + cell_h

                if rec is None:
                    c.create_rectangle(x0, y0, x1, y1, fill="#1f2937", outline="#374151")
                    continue

                val   = rec["averageHashRate"] if mode == "hashrate" else rec["efficiencyJTH"]
                color = heat_color(val)
                c.create_rectangle(x0, y0, x1, y1, fill=color, outline="#111827")

                # store for click detection
                self._hmap_cells[(volt, freq)] = {
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "result": rec
                }

                if cell_w > 38 and cell_h > 16:
                    c.create_text(
                        (x0 + x1) / 2, (y0 + y1) / 2,
                        text=f"{val:.0f}",
                        fill="#111827" if (val - lo) / span > 0.4 else C["text"],
                        font=("Courier", 7),
                    )

        # voltage labels
        for vi, volt in enumerate(voltages):
            y = MT + vi * cell_h + cell_h / 2
            c.create_text(ML - 4, y, text=str(volt),
                          fill=C["muted"], font=("Courier", 7), anchor="e")

        # frequency labels
        for fi, freq in enumerate(freqs):
            x = ML + fi * cell_w + cell_w / 2
            c.create_text(x, H - MB + 8, text=str(freq),
                          fill=C["muted"], font=("Courier", 7), anchor="n")

        # axis titles
        c.create_text(ML - 48, MT + (H - MT - MB) / 2,
                      text="Voltage (mV)", fill=C["muted"],
                      font=("Courier", 7), angle=90)
        c.create_text(ML + (W - ML - MR) / 2, H - 8,
                      text="Frequency (MHz)", fill=C["muted"],
                      font=("Courier", 7))

        # colour scale bar
        bar_x = W - MR - 10
        bar_h = H - MT - MB
        for i in range(bar_h):
            t   = 1 - i / bar_h
            val = lo + t * span
            col = heat_color(val)
            c.create_line(bar_x, MT + i, bar_x + 8, MT + i, fill=col)
        c.create_text(bar_x + 4, MT - 8,
                      text=f"{hi:.0f}", fill=C["muted"], font=("Courier", 6))
        c.create_text(bar_x + 4, MT + bar_h + 8,
                      text=f"{lo:.0f}", fill=C["muted"], font=("Courier", 6))

    # v1.7: Heatmap click handler
    def _on_heatmap_click(self, event):
        """Show detailed popup when user clicks a heatmap cell."""
        x, y = event.x, event.y

        for (volt, freq), cell in self._hmap_cells.items():
            if cell["x0"] <= x <= cell["x1"] and cell["y0"] <= y <= cell["y1"]:
                r = cell["result"]

                err_str = f"{r['averageErrorRate']:.3f}%" if "averageErrorRate" in r and r["averageErrorRate"] is not None else "—"
                vr_str = f"{r['averageVRTemp']:.1f}°C" if "averageVRTemp" in r else "—"
                power = r["averageHashRate"] * r["efficiencyJTH"] / 1000

                msg = (
                    f"Configuration Details\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Voltage:     {r['coreVoltage']} mV\n"
                    f"Frequency:   {r['frequency']} MHz\n\n"
                    f"Hashrate:    {r['averageHashRate']:.1f} GH/s\n"
                    f"Power:       {power:.1f} W\n"
                    f"Efficiency:  {r['efficiencyJTH']:.2f} J/TH\n\n"
                    f"Chip temp:   {r['averageTemperature']:.1f}°C\n"
                    f"VR temp:     {vr_str}\n"
                    f"Error rate:  {err_str}\n\n"
                    f"Stable:      {'✓ Yes' if r.get('stable', True) else '✗ No'}"
                )

                messagebox.showinfo(
                    f"{volt}mV / {freq}MHz",
                    msg,
                    parent=self
                )
                return


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BitaxeBenchGui 1.8")
        self.configure(bg=C["bg"])
        self.resizable(True, True)
        self.minsize(660, 680)

        self._benchmark_thread = None
        self._engine           = None
        self._log_queue        = queue.Queue()

        self._apply_theme()
        self._build_ui()
        self._poll_queue()

    def _apply_theme(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        bg   = C["bg"]
        pnl  = C["panel"]
        txt  = C["text"]
        mute = C["muted"]
        acc  = C["accent"]
        card = C["card"]

        style.configure(".",
            background=bg, foreground=txt,
            fieldbackground=card, troughcolor=pnl,
            selectbackground=acc, selectforeground="#111827",
            font=("Courier", 9),
        )
        style.configure("TFrame",      background=bg)
        style.configure("TLabel",      background=bg, foreground=txt)
        style.configure("TLabelframe", background=pnl, foreground=acc,
                        bordercolor=C["separator"], lightcolor=pnl, darkcolor=pnl)
        style.configure("TLabelframe.Label", background=pnl, foreground=acc,
                        font=("Courier", 9, "bold"))

        style.configure("TEntry",   fieldbackground=card, foreground=txt,
                        insertcolor=txt, bordercolor=C["separator"])
        style.configure("TSpinbox", fieldbackground=card, foreground=txt,
                        insertcolor=txt, arrowcolor=acc, bordercolor=C["separator"])
        style.configure("TRadiobutton", background=bg, foreground=txt,
                        indicatorcolor=acc)
        style.map("TRadiobutton",
            background=[("active", pnl)],
            foreground=[("active", acc)],
        )

        style.configure("TButton",
            background=pnl, foreground=txt,
            bordercolor=C["separator"], lightcolor=pnl, darkcolor=pnl,
            relief="flat", padding=(8, 5),
        )
        style.map("TButton",
            background=[("active", card), ("pressed", C["separator"])],
            foreground=[("active", acc)],
        )

        style.configure("Accent.TButton",
            background=acc, foreground="#111827",
            bordercolor=C["accent_dark"], lightcolor=acc, darkcolor=C["accent_dark"],
            relief="flat", padding=(10, 6), font=("Courier", 9, "bold"),
        )
        style.map("Accent.TButton",
            background=[("active", C["accent_dark"]), ("pressed", C["accent_dark"])],
        )

        style.configure("Danger.TButton",
            background="#7f1d1d", foreground=txt,
            bordercolor="#991b1b", lightcolor="#7f1d1d", darkcolor="#7f1d1d",
            relief="flat", padding=(8, 5),
        )
        style.map("Danger.TButton",
            background=[("active", "#991b1b"), ("disabled", "#374151")],
            foreground=[("disabled", mute)],
        )

        style.configure("TSeparator", background=C["separator"])
        style.configure("TScrollbar",
            background=pnl, troughcolor=bg, arrowcolor=mute,
            bordercolor=bg, lightcolor=pnl, darkcolor=pnl,
        )

        style.configure("Treeview",
            background=C["panel"], foreground=txt,
            fieldbackground=C["panel"], rowheight=22,
            bordercolor=C["separator"],
        )
        style.configure("Treeview.Heading",
            background=C["accent"], foreground="#111827",
            font=("Courier", 8, "bold"), relief="flat",
        )
        style.map("Treeview",
            background=[("selected", acc)],
            foreground=[("selected", "#111827")],
        )

    def _build_ui(self):
        PAD = {"padx": 10, "pady": 4}

        # header
        banner = tk.Frame(self, bg=C["accent"], height=52)
        banner.pack(fill="x")
        banner.pack_propagate(False)
        tk.Label(
            banner, text="⛏  BITAXE ALL CHIP BENCHMARK v1.8",
            bg=C["accent"], fg="#111827",
            font=("Courier", 15, "bold"),
        ).pack(side="left", padx=18, pady=10)
        tk.Label(
            banner, text="open-source ASIC tuning tool",
            bg=C["accent"], fg="#78350f",
            font=("Courier", 8),
        ).pack(side="left", padx=0, pady=16)

        # config frame
        cfg_frame = ttk.LabelFrame(self, text="  Configuration", padding=10)
        cfg_frame.pack(fill="x", padx=12, pady=(10, 4))
        cfg_frame.columnconfigure(1, weight=1)

        row = 0

        # IP with network scanner button
        ttk.Label(cfg_frame, text="Bitaxe IP address:").grid(row=row, column=0, sticky="w", **PAD)
        self._ip_var = tk.StringVar(value=DEFAULTS["ip"])
        ip_frame = ttk.Frame(cfg_frame)
        ip_frame.grid(row=row, column=1, sticky="w", **PAD)

        ip_entry = ttk.Entry(ip_frame, textvariable=self._ip_var, width=22)
        ip_entry.pack(side="left")

        ttk.Button(
            ip_frame, text="🔍 Scan Network",
            command=self._open_network_scanner
        ).pack(side="left", padx=(5, 0))

        row += 1

        ttk.Separator(cfg_frame, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=6
        )
        row += 1

        # Chip detection
        ttk.Label(cfg_frame, text="Chip detection:").grid(row=row, column=0, sticky="w", **PAD)
        self._chip_var = tk.StringVar(value=DEFAULTS["chip_mode"])
        chip_frame = ttk.Frame(cfg_frame)
        chip_frame.grid(row=row, column=1, columnspan=2, sticky="w")
        for label, val in [("Auto (recommended)", "auto"), ("Single chip", "single"), ("Dual chip", "dual")]:
            ttk.Radiobutton(chip_frame, text=label, variable=self._chip_var, value=val).pack(
                side="left", padx=(0, 12)
            )
        row += 1

        ttk.Separator(cfg_frame, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=6
        )
        row += 1

        ttk.Label(cfg_frame, text="Starting settings", font=("Courier", 9, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", **PAD
        )
        row += 1

        fields = [
            ("Initial voltage (mV):",      "_v_voltage",    DEFAULTS["voltage"],             MIN_ALLOWED_VOLTAGE, MAX_ALLOWED_VOLTAGE),
            ("Initial frequency (MHz):",   "_v_frequency",  DEFAULTS["frequency"],           MIN_ALLOWED_FREQ,    MAX_ALLOWED_FREQ),
            ("PSU max wattage (W):",        "_v_psu",        DEFAULTS["max_psu_watts"],       10,                  500),
            ("Max chip temp (°C):",         "_v_max_temp",   DEFAULTS["max_temp"],            40,                  90),
            ("Max VR temp (°C):",           "_v_max_vr",     DEFAULTS["max_vr_temp"],         40,                  110),
            ("Voltage step (mV):",          "_v_v_step",     DEFAULTS["voltage_increment"],   5,                   100),
            ("Frequency step (MHz):",       "_v_f_step",     DEFAULTS["frequency_increment"], 5,                   100),
            ("Max voltage (mV):",           "_v_max_voltage",DEFAULTS["max_voltage"],         MIN_ALLOWED_VOLTAGE, MAX_ALLOWED_VOLTAGE),
            ("Max frequency (MHz):",        "_v_max_freq",   DEFAULTS["max_frequency"],       MIN_ALLOWED_FREQ,    MAX_ALLOWED_FREQ),
            ("Max error rate (%):",         "_v_err_max",    DEFAULTS["err_max_valid"],       0,                   10),
            ("Early-stop steps (0=off):",   "_v_early_stop", DEFAULTS["early_stop_steps"],    0,                   10),
            ("Auto-save interval (steps):", "_v_autosave",   DEFAULTS["autosave_interval"],   0,                   50),  # v1.7
        ]

        for label, attr, default, lo, hi in fields:
            ttk.Label(cfg_frame, text=label).grid(row=row, column=0, sticky="w", **PAD)
            if attr == "_v_err_max":
                var = tk.DoubleVar(value=default)
            else:
                var = tk.IntVar(value=default)
            setattr(self, attr, var)
            spin = ttk.Spinbox(cfg_frame, from_=lo, to=hi, textvariable=var, width=8,
                               increment=0.1 if attr == "_v_err_max" else 1)
            spin.grid(row=row, column=1, sticky="w", **PAD)
            row += 1

        # Adaptive warm-up
        self._v_adaptive = tk.BooleanVar(value=DEFAULTS["adaptive_warmup"])
        ttk.Checkbutton(cfg_frame, text="Adaptive warm-up (wait for temp stability)",
                        variable=self._v_adaptive).grid(
            row=row, column=0, columnspan=2, sticky="w", **PAD)
        row += 1

        # v1.7: Preset management row
        preset_frame = ttk.Frame(cfg_frame)
        preset_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 2))

        ttk.Label(preset_frame, text="Presets:", font=("Courier", 9, "bold")).pack(side="left", padx=(0, 8))
        ttk.Button(preset_frame, text="💾 Save", command=self._save_preset).pack(side="left", padx=(0, 4))
        ttk.Button(preset_frame, text="📂 Load", command=self._load_preset).pack(side="left", padx=(0, 4))
        ttk.Button(preset_frame, text="🗑 Delete", command=self._delete_preset).pack(side="left")
        row += 1

        # Buttons row
        btn_frame = ttk.Frame(cfg_frame)
        btn_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 2))

        ttk.Button(btn_frame, text="↺  Reset", command=self._reset_defaults).pack(
            side="left", padx=(0, 6)
        )
        self._start_btn = ttk.Button(
            btn_frame, text="▶  Start Benchmark",
            command=self._start, style="Accent.TButton"
        )
        self._start_btn.pack(side="left", padx=(0, 6))

        ttk.Button(
            btn_frame, text="⏭  Resume",
            command=self._resume, style="Accent.TButton"
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            btn_frame, text="📊  Analyse Results",
            command=self._open_analysis, style="Accent.TButton"
        ).pack(side="left", padx=(0, 6))

        # v1.7: Comparison button
        ttk.Button(
            btn_frame, text="⚖  Compare",
            command=self._open_comparison, style="Accent.TButton"
        ).pack(side="left", padx=(0, 6))

        # v1.8: Monitor Control button
        ttk.Button(
            btn_frame, text="📡 Monitor",
            command=self._open_monitor, style="Accent.TButton"
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            btn_frame, text="📄  Export CSV",
            command=self._export_csv,
        ).pack(side="left")

        # status bar
        self._status_var = tk.StringVar(value="Idle — configure above and press Start.")
        status_bar = tk.Label(
            self, textvariable=self._status_var,
            bg=C["panel"], fg=C["muted"],
            anchor="w", padx=8, pady=4,
            font=("Courier", 8),
        )
        status_bar.pack(fill="x", padx=12, pady=(4, 0))

        # progress bar
        prog_frame = tk.Frame(self, bg=C["bg"])
        prog_frame.pack(fill="x", padx=12, pady=(4, 0))
        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(
            prog_frame, variable=self._progress_var,
            maximum=100, mode="determinate", length=400,
        )
        self._progress_bar.pack(side="left", fill="x", expand=True)
        self._eta_var = tk.StringVar(value="")
        tk.Label(prog_frame, textvariable=self._eta_var,
                 bg=C["bg"], fg=C["muted"], font=("Courier", 8), width=22,
                 anchor="e").pack(side="left", padx=(8, 0))

        # live hashrate chart
        chart_frame = ttk.LabelFrame(self, text="  Live Hashrate (GH/s)", padding=4)
        chart_frame.pack(fill="x", padx=12, pady=(6, 0))
        self._chart_canvas = tk.Canvas(
            chart_frame, bg=C["log_bg"], height=90,
            highlightthickness=0,
        )
        self._chart_canvas.pack(fill="x")
        self._chart_data: list[float] = []

        # log area
        log_frame = ttk.LabelFrame(self, text="  Output", padding=4)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(4, 4))

        self._log_text = scrolledtext.ScrolledText(
            log_frame, state="disabled", wrap="word",
            font=("Courier", 9), height=16,
            bg=C["log_bg"], fg=C["text"],
            insertbackground=C["text"],
            selectbackground=C["accent"],
            relief="flat", borderwidth=0,
        )
        self._log_text.pack(fill="both", expand=True)

        self._log_text.tag_config("green",  foreground=C["green"])
        self._log_text.tag_config("yellow", foreground=C["yellow"])
        self._log_text.tag_config("red",    foreground=C["red"])
        self._log_text.tag_config("white",  foreground=C["text"])

        # bottom bar
        bot_frame = tk.Frame(self, bg=C["bg"])
        bot_frame.pack(fill="x", padx=12, pady=(0, 10))

        self._stop_btn = ttk.Button(
            bot_frame, text="⏹  Stop Benchmark",
            command=self._stop, state="disabled",
            style="Danger.TButton",
        )
        self._stop_btn.pack(side="left")

        ttk.Button(bot_frame, text="🗑  Clear log", command=self._clear_log).pack(
            side="left", padx=8
        )

    # v1.8: Network Scanner
    def _open_network_scanner(self):
        """Open network scanner window."""
        def on_device_selected(ip):
            self._ip_var.set(ip)
            self._append_log(f"Selected device: {ip}", "green")

        win = NetworkScannerWindow(self, on_select_callback=on_device_selected)
        win.grab_set()

    # v1.8: Monitor Control
    def _open_monitor(self):
        """Open Monitor Control window."""
        ip = self._ip_var.get().strip()
        win = MonitorControlWindow(self, ip)
        win.grab_set()

    # v1.7: Preset management
    def _save_preset(self):
        name = simpledialog.askstring("Save Preset", "Enter preset name:", parent=self)
        if not name:
            return

        config = {
            "ip": self._ip_var.get(),
            "chip_mode": self._chip_var.get(),
            "voltage": self._v_voltage.get(),
            "frequency": self._v_frequency.get(),
            "max_psu_watts": self._v_psu.get(),
            "max_temp": self._v_max_temp.get(),
            "max_vr_temp": self._v_max_vr.get(),
            "voltage_increment": self._v_v_step.get(),
            "frequency_increment": self._v_f_step.get(),
            "max_voltage": self._v_max_voltage.get(),
            "max_frequency": self._v_max_freq.get(),
            "err_max_valid": self._v_err_max.get(),
            "early_stop_steps": self._v_early_stop.get(),
            "adaptive_warmup": self._v_adaptive.get(),
            "autosave_interval": self._v_autosave.get(),
        }

        try:
            PresetManager.save_preset(name, config)
            self._append_log(f"Preset '{name}' saved.", "green")
            messagebox.showinfo("Saved", f"Preset '{name}' saved successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Cannot save preset:\n{e}")

    def _load_preset(self):
        presets = PresetManager.list_presets()
        if not presets:
            messagebox.showinfo("No Presets", "No saved presets found.")
            return

        # Simple selection dialog
        top = tk.Toplevel(self)
        top.title("Load Preset")
        top.configure(bg=C["bg"])
        top.resizable(False, False)

        tk.Label(top, text="Select preset:", bg=C["bg"], fg=C["text"],
                 font=("Courier", 10, "bold")).pack(padx=20, pady=(10, 5))

        listbox = tk.Listbox(top, height=10, width=30, bg=C["panel"], fg=C["text"],
                             selectbackground=C["accent"], font=("Courier", 9))
        listbox.pack(padx=20, pady=5)

        for p in presets:
            listbox.insert("end", p)

        def load_selected():
            sel = listbox.curselection()
            if not sel:
                return
            name = listbox.get(sel[0])
            config = PresetManager.load_preset(name)
            if config:
                self._ip_var.set(config.get("ip", ""))
                self._chip_var.set(config.get("chip_mode", "auto"))
                self._v_voltage.set(config.get("voltage", 1150))
                self._v_frequency.set(config.get("frequency", 500))
                self._v_psu.set(config.get("max_psu_watts", 60))
                self._v_max_temp.set(config.get("max_temp", 66))
                self._v_max_vr.set(config.get("max_vr_temp", 86))
                self._v_v_step.set(config.get("voltage_increment", 20))
                self._v_f_step.set(config.get("frequency_increment", 25))
                self._v_max_voltage.set(config.get("max_voltage", 1400))
                self._v_max_freq.set(config.get("max_frequency", 1200))
                self._v_err_max.set(config.get("err_max_valid", 1.0))
                self._v_early_stop.set(config.get("early_stop_steps", 3))
                self._v_adaptive.set(config.get("adaptive_warmup", True))
                self._v_autosave.set(config.get("autosave_interval", 10))
                self._append_log(f"Preset '{name}' loaded.", "green")
                top.destroy()

        btn_frame = tk.Frame(top, bg=C["bg"])
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Load", command=load_selected).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=top.destroy).pack(side="left", padx=5)

    def _delete_preset(self):
        presets = PresetManager.list_presets()
        if not presets:
            messagebox.showinfo("No Presets", "No saved presets found.")
            return

        top = tk.Toplevel(self)
        top.title("Delete Preset")
        top.configure(bg=C["bg"])
        top.resizable(False, False)

        tk.Label(top, text="Select preset to delete:", bg=C["bg"], fg=C["text"],
                 font=("Courier", 10, "bold")).pack(padx=20, pady=(10, 5))

        listbox = tk.Listbox(top, height=10, width=30, bg=C["panel"], fg=C["text"],
                             selectbackground=C["accent"], font=("Courier", 9))
        listbox.pack(padx=20, pady=5)

        for p in presets:
            listbox.insert("end", p)

        def delete_selected():
            sel = listbox.curselection()
            if not sel:
                return
            name = listbox.get(sel[0])
            if messagebox.askyesno("Confirm", f"Delete preset '{name}'?", parent=top):
                try:
                    PresetManager.delete_preset(name)
                    self._append_log(f"Preset '{name}' deleted.", "yellow")
                    top.destroy()
                except Exception as e:
                    messagebox.showerror("Error", f"Cannot delete preset:\n{e}", parent=top)

        btn_frame = tk.Frame(top, bg=C["bg"])
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Delete", command=delete_selected).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=top.destroy).pack(side="left", padx=5)

    def _reset_defaults(self):
        self._ip_var.set(DEFAULTS["ip"])
        self._chip_var.set(DEFAULTS["chip_mode"])
        self._v_voltage.set(DEFAULTS["voltage"])
        self._v_frequency.set(DEFAULTS["frequency"])
        self._v_psu.set(DEFAULTS["max_psu_watts"])
        self._v_max_temp.set(DEFAULTS["max_temp"])
        self._v_max_vr.set(DEFAULTS["max_vr_temp"])
        self._v_v_step.set(DEFAULTS["voltage_increment"])
        self._v_f_step.set(DEFAULTS["frequency_increment"])
        self._v_max_voltage.set(DEFAULTS["max_voltage"])
        self._v_max_freq.set(DEFAULTS["max_frequency"])
        self._v_err_max.set(DEFAULTS["err_max_valid"])
        self._v_early_stop.set(DEFAULTS["early_stop_steps"])
        self._v_adaptive.set(DEFAULTS["adaptive_warmup"])
        self._v_autosave.set(DEFAULTS["autosave_interval"])
        self._append_log("Settings reset to defaults.", "yellow")

    def _append_log(self, msg: str, color: str = "white"):
        self._log_text.config(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_text.insert("end", f"[{ts}] {msg}\n", color)
        self._log_text.see("end")
        self._log_text.config(state="disabled")

    def _clear_log(self):
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.config(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                item = self._log_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    _, msg, color = item
                    self._append_log(msg, color)
                elif kind == "status":
                    _, msg = item
                    self._status_var.set(msg)
                elif kind == "progress":
                    _, pct, eta_str = item
                    self._progress_var.set(pct)
                    self._eta_var.set(eta_str)
                elif kind == "chart":
                    _, hr_value = item
                    # v1.7: Fix C — limit chart data to prevent overflow
                    self._chart_data.append(hr_value)
                    if len(self._chart_data) > CHART_MAX_POINTS:
                        self._chart_data.pop(0)
                    self._redraw_chart()
                elif kind == "done":
                    self._on_benchmark_done()
        except queue.Empty:
            pass
        self.after(200, self._poll_queue)

    def _redraw_chart(self):
        c = self._chart_canvas
        c.delete("all")
        data = self._chart_data
        if len(data) < 2:
            return
        w = c.winfo_width() or 600
        h = c.winfo_height() or 90
        margin = 6
        lo, hi = min(data), max(data)
        span = hi - lo if hi != lo else 1.0

        def x(i):
            return margin + (i / (len(data) - 1)) * (w - 2 * margin)

        def y(v):
            return margin + (1 - (v - lo) / span) * (h - 2 * margin)

        for pct in [0.25, 0.5, 0.75]:
            yg = margin + (1 - pct) * (h - 2 * margin)
            c.create_line(margin, yg, w - margin, yg, fill="#1f2937", width=1)

        pts = []
        for i, v in enumerate(data):
            pts.extend([x(i), y(v)])
        if len(pts) >= 4:
            c.create_line(*pts, fill=C["accent"], width=2, smooth=True)

        last = data[-1]
        c.create_text(w - margin - 2, y(last) - 8,
                      text=f"{last:.0f}", fill=C["accent"],
                      font=("Courier", 7), anchor="e")

    def _validate(self) -> dict | None:
        ip = self._ip_var.get().strip()
        if not ip:
            messagebox.showerror("Missing IP", "Please enter the Bitaxe IP address.")
            return None

        try:
            v          = self._v_voltage.get()
            f          = self._v_frequency.get()
            psu        = self._v_psu.get()
            mt         = self._v_max_temp.get()
            mvr        = self._v_max_vr.get()
            v_step     = self._v_v_step.get()
            f_step     = self._v_f_step.get()
            max_v      = self._v_max_voltage.get()
            max_f      = self._v_max_freq.get()
            err_max    = self._v_err_max.get()
            early_stop = self._v_early_stop.get()
            autosave   = self._v_autosave.get()
        except tk.TclError:
            messagebox.showerror("Invalid input", "All numeric fields must be valid numbers.")
            return None

        errors = []
        if not (MIN_ALLOWED_VOLTAGE <= v <= MAX_ALLOWED_VOLTAGE):
            errors.append(f"Voltage must be {MIN_ALLOWED_VOLTAGE}–{MAX_ALLOWED_VOLTAGE} mV.")
        if not (MIN_ALLOWED_FREQ <= f <= MAX_ALLOWED_FREQ):
            errors.append(f"Frequency must be {MIN_ALLOWED_FREQ}–{MAX_ALLOWED_FREQ} MHz.")
        if psu < 10:
            errors.append("PSU wattage must be ≥ 10 W.")
        if not (40 <= mt <= 90):
            errors.append("Max chip temp must be 40–90 °C.")
        if not (40 <= mvr <= 110):
            errors.append("Max VR temp must be 40–110 °C.")
        if not (5 <= v_step <= 100):
            errors.append("Voltage step must be 5–100 mV.")
        if not (5 <= f_step <= 100):
            errors.append("Frequency step must be 5–100 MHz.")
        if not (MIN_ALLOWED_VOLTAGE <= max_v <= MAX_ALLOWED_VOLTAGE):
            errors.append(f"Max voltage must be {MIN_ALLOWED_VOLTAGE}–{MAX_ALLOWED_VOLTAGE} mV.")
        if max_v < v:
            errors.append("Max voltage must be ≥ initial voltage.")
        if not (MIN_ALLOWED_FREQ <= max_f <= MAX_ALLOWED_FREQ):
            errors.append(f"Max frequency must be {MIN_ALLOWED_FREQ}–{MAX_ALLOWED_FREQ} MHz.")
        if max_f < f:
            errors.append("Max frequency must be ≥ initial frequency.")
        if not (0.0 < err_max <= 10.0):
            errors.append("Max error rate must be between 0.1 and 10 %.")
        if errors:
            messagebox.showerror("Validation error", "\n".join(errors))
            return None

        return {
            "ip":                  ip,
            "voltage":             v,
            "frequency":           f,
            "max_psu_watts":       psu,
            "max_temp":            mt,
            "max_vr_temp":         mvr,
            "chip_mode":           self._chip_var.get(),
            "voltage_increment":   v_step,
            "frequency_increment": f_step,
            "max_voltage":         max_v,
            "max_frequency":       max_f,
            "err_max_valid":       err_max,
            "early_stop_steps":    early_stop,
            "adaptive_warmup":     self._v_adaptive.get(),
            "autosave_interval":   autosave,
            "resume_results":      [],
        }

    def _start(self):
        cfg = self._validate()
        if cfg is None:
            return

        self._chart_data.clear()
        self._progress_var.set(0)
        self._eta_var.set("")
        self._chart_canvas.delete("all")

        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._status_var.set("Benchmark running…")
        self._append_log("=" * 54, "white")
        self._append_log("Benchmark started.", "green")

        self._log_queue = queue.Queue()
        self._engine    = BitaxeBenchmark(cfg, self._log_queue)

        self._benchmark_thread = threading.Thread(
            target=self._engine.run, daemon=True
        )
        self._benchmark_thread.start()

    def _resume(self):
        path = filedialog.askopenfilename(
            title="Select partial benchmark JSON to resume",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            prior = data.get("all_results", [])
        except Exception as e:
            messagebox.showerror("Error", f"Cannot read file:\n{e}")
            return

        cfg = self._validate()
        if cfg is None:
            return

        cfg["resume_results"] = prior
        skipped = len(prior)
        self._append_log(f"Resume: skipping {skipped} already-tested steps.", "yellow")

        self._chart_data.clear()
        self._progress_var.set(0)
        self._eta_var.set("")
        self._chart_canvas.delete("all")

        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._status_var.set("Benchmark resuming…")

        self._log_queue = queue.Queue()
        self._engine    = BitaxeBenchmark(cfg, self._log_queue)
        self._benchmark_thread = threading.Thread(
            target=self._engine.run, daemon=True
        )
        self._benchmark_thread.start()

    def _export_csv(self):
        results = None
        if self._engine and self._engine.results:
            results = self._engine.results
        else:
            path = filedialog.askopenfilename(
                title="Select benchmark JSON to export",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            )
            if not path:
                return
            try:
                with open(path) as f:
                    data = json.load(f)
                results = data.get("all_results", [])
            except Exception as e:
                messagebox.showerror("Error", f"Cannot read file:\n{e}")
                return

        if not results:
            messagebox.showwarning("No data", "No results to export.")
            return

        save_path = filedialog.asksaveasfilename(
            title="Save CSV as",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not save_path:
            return

        fieldnames = ["coreVoltage", "frequency", "averageHashRate",
                      "averageTemperature", "efficiencyJTH", "stable",
                      "averageVRTemp", "averageErrorRate", "profile"]
        try:
            with open(save_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(results)
            self._append_log(f"CSV exported → {save_path}", "green")
        except Exception as e:
            messagebox.showerror("Error", f"Cannot write CSV:\n{e}")

    def _stop(self):
        if self._engine:
            self._engine.stop_event.set()
            self._append_log("Stop requested — finishing current sample…", "yellow")
            self._stop_btn.config(state="disabled")

    def _on_benchmark_done(self):
        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._append_log("─ Benchmark finished ─", "green")
        self._progress_var.set(100)
        self._eta_var.set("Done!")
        try:
            if _HAS_WINSOUND:
                winsound.MessageBeep(winsound.MB_ICONINFORMATION)
            else:
                self.bell()
        except Exception:
            pass

    def _open_analysis(self):
        AnalysisWindow(self)

    # v1.7: Open comparison window
    def _open_comparison(self):
        ComparisonWindow(self)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = App()
    app.mainloop()
