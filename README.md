# TeamAutoflow

Welcome to the code repository for our capstone team project for Team Autoflow!
This repo contains all code used on the computer and the data acquisition modules, along with their configuration.

The bench motors a Honda CBR600RR engine with a Predator engine (the prime mover), holds it at a set RPM and throttle
opening, and records in-cylinder pressure to estimate **volumetric efficiency (VE)** across an RPM × throttle map.

---

## Quick start

```bash
git clone https://github.com/rileypogue/TeamAutoflow.git
cd TeamAutoflow
uv sync                          # installs the Python dependencies from uv.lock
cd scripts
uv run python main_gui.py        # run from scripts/ (data goes to ../data)
```

Upload the ESP32 firmware from the `scripts` directory (Windows: use `COMx` instead of `/dev/ttyUSB0`; find it with `arduino-cli board list`):

```bash
arduino-cli compile --fqbn esp32:esp32:esp32c6 PID_controller
arduino-cli upload  --fqbn esp32:esp32:esp32c6 -p /dev/ttyUSB0 PID_controller
```

---

## Setup (first time only)

**Both systems**
- **uv**: https://docs.astral.sh/uv/ (Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, then reopen PowerShell)
- **LabJack LJM driver** from LabJack's website (Support → Software → LJM software installers). The `labjack-ljm`
  Python package is only a wrapper. On Windows this also installs **Kipling**; close it before starting the GUI.
- **ESP32 core 3.x + BMP280 library** (the firmware uses the core 3.x `ledcAttach`/`ledcWrite` API and won't compile on 2.x):
  ```bash
  arduino-cli core update-index --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
  arduino-cli core install esp32:esp32 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
  arduino-cli lib install "Adafruit BMP280 Library"
  ```
  arduino-cli: https://arduino.github.io/arduino-cli/ (or use the Arduino IDE, below).

**Linux only**
```bash
sudo apt install python3-tk                # Tkinter comes from the system, not pip
sudo usermod -aG dialout $USER             # serial port access; log out and back in
```

**Windows 10 only**
- **Git**: https://git-scm.com/download/win (defaults are fine).
- **ESP32 USB driver**: if no COM port appears in *Device Manager → Ports*, install the CP210x (Silicon Labs) or
  CH343 (WCH) driver, or use the board's native **USB** port (no driver needed).

### Uploading with the Arduino IDE

One-time: *File → Preferences → Additional boards manager URLs* →
`https://espressif.github.io/arduino-esp32/package_esp32_index.json`, then install **"esp32 by Espressif Systems" 3.x**
(*Boards Manager*) and **"Adafruit BMP280 Library"** with its dependencies (*Manage Libraries*).

Each upload:
1. Open `scripts/PID_controller/PID_controller.ino` (folder and `.ino` names must match).
2. *Tools → Board →* **ESP32C6 Dev Module**, *Tools → Port →* the ESP32's port.
3. *Tools → USB CDC On Boot*: **Disabled** for the board's **UART** port, **Enabled** for its native **USB** port
   (otherwise the GUI never sees `Serial` output).
4. **Upload**. If it can't connect: hold **BOOT**, tap **RESET**, release BOOT, upload again.
5. Close the Serial Monitor before starting the GUI (only one program can use the port).

### Managing dependencies

`uv add <package>` adds a dependency, `uv lock --check` verifies the lock file, `uv sync` installs it after pulling.
Commit `pyproject.toml` and `uv.lock` together. Package names that differ from imports: `serial` → `pyserial`,
`labjack` → `labjack-ljm`.

---

## Repository layout

```
TeamAutoflow/
├── README.md, pyproject.toml, uv.lock
├── scripts/                   # run everything from here
│   ├── main_gui.py            # operator GUI: run control, live readout, auto sweep, analysis
│   ├── DAQ.py                 # LabJack T7 stream logger (background thread)
│   ├── analyze_run.py         # VE analysis of a run folder
│   ├── PID_controller/        # ESP32-C6 firmware: throttle control, RPM, ambient, serial protocol
│   ├── throttle/              # <describe: e.g. standalone throttle body test sketch>
│   └── bmp_diag/              # BMP280 I2C diagnostic sketch
└── data/
    ├── run_<MM-DD_HH-MM-SS>/  # one folder per run (<name>_<time> if a run name was set)
    └── wiring.yaml            # WireViz wiring diagram (+ generated .svg / .png / .html / .bom.tsv)
```

Currently, all run data is listed in .gitignore as the files (specifically lj.bin) will get very large. Later, this
project will be integrated with InfluxDB so data will be available to all Mines Formula SAE members.

---

## Running the bench

1. Turn the **key** on and release the **E-stop** (the relay bank only energizes with both).
2. Start the GUI (`cd scripts && uv run python main_gui.py`). It finds the ESP32 and enables **START**.
3. Enter a **run name** and press *Send*.
4. Control manually (servo angle / throttle %), or fill in **Auto sweep** and turn on *Run sweep when START is pressed*:
   RPM setpoints `1500,2000,2500` or `1000:3000:500` (start:stop:step), throttle % `10,20,40,60,100`
   (blank = leave alone), dwell seconds per point (default 5).
5. Press **START**; press **STOP** (or let the sweep finish) to end.
6. **Analyze a run...** → pick the run folder, then the tare folder. The VE heatmap opens when done.

Close the GUI with the **window close button** (not Ctrl-C) so it stops the LabJack stream and parks the servo.

**Tare runs** measure each pressure sensor's zero offset: engine **stopped for at least a minute**, same sensors and
wiring, ~5-10 s (START → wait → STOP). Redo after rewiring or sensor swaps, ideally the same day.

---

## Data files (one folder per run)

| File | Contents |
|---|---|
| `lj.bin` | LabJack stream, `float32`: `np.fromfile(f, np.float32).reshape(-1, 5)` → `AIN0-3, FIO_STATE`. Crank signal = bit 0: `data[:, 4].astype(np.uint16) & 1` (LOW during each tooth pulse) |
| `meta.json` | channels, actual scan rate, start times, sweep settings, stream errors/retries |
| `esp.csv` | ESP32 log `esp_us_time,type,value`: `RPM`, `GAPS` (crank sync, 1 Hz), `tps1` / `pos` / `target` / `cmd` (throttle, 25 Hz), `amb_T` / `amb_P` (1 Hz), `START` / `STOP` / `MARK` |
| `steps.csv` | sweeps only: one row per point (targets, settled flag, time mark) |
| `results.csv`, `ve_heatmap.png`, `traces.png` | written by `analyze_run.py` |

---

## Analysis method (`analyze_run.py`)

```bash
uv run python analyze_run.py ../data/<run folder> --tare ../data/<tare folder>   # --amb-kpa 81.7 if no BMP280 data
```

The transducers are **gauge** sensors and can't read vacuum, so the script uses each cylinder's **peak motored
compression pressure**: `VE ≈ (P_peak_abs / P_ambient) / CR^n` (= 1.0 for a full charge at ambient). Valid only while
the CBR is **motored, not firing**. Channels averaging below 0.3 V are skipped and noted on the heatmap.

---

## Hardware

- **LabJack T7 Pro** (https://labjack.com/products/labjack-t7-pro): pressure transducers on AIN0-AIN3 (±10 V,
  15 kHz per channel); crank signal on FIO0
- **4 × RIFE 200 PSI pressure transducers**, 1/8" NPT (gauge, 0.5-4.5 V, ratiometric to 5 V), one per cylinder
- **Adafruit BMP280**: ambient pressure and temperature over I2C (address 0x77)
- **CBR600RR crank position sensor** (2-wire VR) → **LM1815** interface → one digital pulse per tooth, shared by the ESP32 and LabJack
- **ESP32-C6-DevKitC-1**: controls the Bosch throttle body (closed-loop PI + spring feedforward) and the Predator
  throttle servo (RPM hold), reads RPM and ambient (1 Hz). Planned: monitor the 5 V bus for the ratiometric transducers
- **Bosch 32 mm electronic throttle body** (0280.750.148) driven by a **BTS7960 (IBT-2)** H-bridge
- **ZOSKAY 40 kg servo** on the Predator throttle
- 5 V / 12 V bench supply, star-point grounding, key switch + E-stop + start button

---

## Firmware (ESP32-C6)

| GPIO | Function |
|---|---|
| 4 | Crank signal in (LM1815 output, falling edge = tooth) |
| 5 | Predator throttle servo PWM (50 Hz, 500-2500 µs) |
| 10 / 11 | H-bridge RPWM / LPWM |
| 22 / 23 | H-bridge R_EN / L_EN |
| 2 / 3 | TPS1 / TPS2 (ADC, 10k/15k dividers) |
| 18 / 19 | I2C SDA / SCL (BMP280) |

**Serial protocol (115200 baud):** `#` lines are status/errors; data lines are `time_us,TYPE,value`. At boot the
firmware prints `# READY throttle_calibrated=<0|1> ambient=<0|1>`. Commands: `START` / `STOP` (logging),
`SPEED <0-180>` (servo angle), `THROTTLE <0-100>` (throttle target, starts closed-loop control), `MARK <n>` (sweep step
marker), `CAL` (calibrate throttle body; plate moves), `RESET` (clear a throttle fault).

---

## Wiring diagram

`data/wiring.yaml` is a [WireViz](https://github.com/wireviz/WireViz) file and the source of truth for pins and wiring.
From `TeamAutoflow`: `uv run wireviz data/wiring.yaml`. Needs `uv add --dev wireviz` and **Graphviz**
(Linux: `sudo apt install graphviz`; Windows: https://graphviz.org/download/, add to PATH).

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "LabJack stream failed to start after 3 tries" | Real error is in the run's `meta.json` → `"retries"`. Usually another program has the T7 open, or a stream was left running. |
| T7 **STATUS** LED blinking continuously | A stream is still active. Close other programs (Linux `ps aux \| grep -i python`, Windows `tasklist \| findstr python` → `taskkill /F /PID <pid>`; also Kipling), or unplug the T7's USB **and** Ethernet for 5 s. |
| "ESP32 not found" | USB cable (many are charge-only), `dialout` group (Linux), driver (Windows), or a serial monitor holding the port. |
| Serial not working but LED is on ESP32 | Check for header pin over J5 on ESP32. Those pins must be jumped for chip to get power. |
| `Access is denied` / `could not open port` | Another program has the port open. Only one program can use it at a time. |
| Ambient temp/pressure show `--` | BMP280 didn't start at boot (`ambient=0` in the READY line). Check its wiring (VIN, GND, SDA, SCL), then power-cycle the ESP32. |
| A pressure channel reads negative (e.g. −0.39 V) | Input floating: signal wire not reaching the T7 or sensor unpowered. Swap channels to see if it follows the sensor, then meter the sensor output and wire. |
| Throttle shows `--` | Not calibrated, or control not on yet (`pos` is only logged while control is active). |
| `# FAULT TPS sensors disagree...` | Throttle body connector/wiring or a worn sensor track. Fix, then send `RESET`. |
| Noisy pressure readings | Twist each transducer's wires, route away from motor/servo/starter wiring; consider T7 differential inputs and a USB isolator on the ESP32. |
| Windows: `uv` / `arduino-cli` / `dot` not recognized | Not on PATH: reopen the terminal, or add the folder under *Environment Variables → Path*. |
