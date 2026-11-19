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
uv run python main_gui.py        # the GUI expects to run from scripts/ (data goes to ../data)
```

Upload the ESP32 firmware (see [Firmware](#firmware-esp32-c6)) from the scripts directory:

```bash
arduino-cli compile --fqbn esp32:esp32:esp32c6 PID_controller 
arduino-cli upload  --fqbn esp32:esp32:esp32c6 -p /dev/ttyUSB0 PID_controller
```

or (Windows): 
```
arduino-cli upload --fqbn esp32:esp32:esp32c6 -p COM5 PID_controller
```

check device manager for specific COM port.


```
```
---

## Managing dependencies

```bash
uv add <package>       # add a dependency (updates pyproject.toml + uv.lock)
uv lock --check        # verify uv.lock matches pyproject.toml
uv sync                # install exactly what uv.lock says (after pulling changes)
```

Commit `pyproject.toml` and `uv.lock` together. Import names that differ from package names:
`serial` → `pyserial`, `labjack` → `labjack-ljm`.

---

## Repository layout

```
TeamAutoflow/
├── README.md
├── pyproject.toml
├── uv.lock
├── scripts/                   # run everything from here (the GUI writes runs to ../data)
│   ├── main_gui.py            # operator GUI: run control, live readout, auto sweep, analysis button
│   ├── DAQ.py                 # LabJack T7 stream logger (background thread)
│   ├── analyze_run.py         # VE analysis of a run folder (called by the GUI, or run by hand)
│   ├── PID_controller/        # ESP32-C6 firmware: throttle control, RPM, ambient, serial protocol
│   ├── throttle/              # <describe: e.g. standalone throttle body test sketch>
│   └── bmp_diag/              # BMP280 I2C diagnostic sketch
└── data/
    ├── run_<MM-DD_HH-MM-SS>/  # one folder per run, created by the GUI (<name>_<time> if a run name was set)
    ├── wiring.yaml            # WireViz wiring diagram (source of truth for pins and wiring)
    └── wiring.svg / .png / .html / .bom.tsv   # generated from wiring.yaml (bom = bill of materials)
```

Currently, all run data is listed in .gitignore as the files (specifically lj.bin) will get very large. Later, this project will be integrated with influxdb so data will be available to all Mines Formula SAE members.  

---

## Running the bench

1. Turn the **key** on and make sure the **E-stop** is released (the relay bank only energizes with both).
2. Start the GUI: `cd scripts && uv run python main_gui.py`. It finds the ESP32 automatically and enables
   **START** once connected. Press **Reconnect ESP32** if it was unplugged.
3. Enter a **run name** and press *Send* (each run gets its own folder `data/<name>_<date>_<time>/`).
4. Either control things manually (servo angle / throttle %), or fill in the **Auto sweep** fields and turn on
   *Run sweep when START is pressed*:
   - RPM setpoints: `1500,2000,2500` or `1000:3000:500` (start:stop:step)
   - Throttle %: `10,20,40,60,100` (blank = leave the throttle alone)
   - Dwell: seconds recorded at each point once it settles (default 5)
5. Press **START**. Press **STOP** (or let the sweep finish) to end the run.
6. Press **Analyze a run...**, pick the run folder, then the tare folder. The VE heatmap opens when done.

Always close the GUI with the **window close button** (not Ctrl-C): that stops the LabJack stream and parks the servo.

### Tare runs

A tare run measures each pressure sensor's zero offset. Record one with the **engine stopped for at least a minute**,
same sensors and wiring, ~5-10 s long (START → wait → STOP). Redo it after any rewiring or sensor swap, ideally the same day.

---

## Data files (one folder per run)

| File | Written by | Contents |
|---|---|---|
| `lj.bin` | `DAQ.py` | LabJack stream, `float32`, interleaved by scan: `np.fromfile(f, np.float32).reshape(-1, 5)` → columns `AIN0, AIN1, AIN2, AIN3, FIO_STATE` |
| `meta.json` | `DAQ.py` | channels, actual scan rate, PC start times, sweep settings, stream errors/retries |
| `esp.csv` | GUI | ESP32 log: `esp_us_time,type,value` |
| `steps.csv` | GUI (sweeps only) | one row per sweep point: target RPM / throttle, settled flag, time mark |
| `results.csv` | `analyze_run.py` | per step: RPM, throttle, ambient, per-cylinder peak pressure, pressure ratio, VE |
| `ve_heatmap.png` | `analyze_run.py` | mean VE estimate by RPM × throttle |
| `traces.png` | `analyze_run.py` | pressure traces with detected peaks (sanity check) |

`FIO_STATE` holds all FIO lines as one number; the crank signal is **bit 0**:
`ckp = data[:, 4].astype(np.uint16) & 1` (the LM1815 output is LOW during each tooth pulse).

### ESP32 log types (`esp.csv`)

| Type | Rate | Meaning |
|---|---|---|
| `RPM` | each new measurement (10 Hz `0` when stopped) | engine speed |
| `GAPS` | 1 Hz | missing-tooth gaps seen (crank sync diagnostic) |
| `tps1` | 25 Hz | raw throttle position sensor 1 (0-1 of ADC range) |
| `pos`, `target`, `cmd` | 25 Hz, while throttle control is on | plate position 0-1, target 0-1, motor command |
| `amb_T`, `amb_P` | 1 Hz | ambient °C and kPa (BMP280) |
| `TARGET`, `THR_TARGET` | on change | servo angle / throttle % commanded |
| `START`, `STOP`, `MARK` | on command | run start/end, sweep step markers |

---

## Analysis method (`analyze_run.py`)

```bash
uv run python analyze_run.py ../data/<run folder> --tare ../data/<tare folder>
# --amb-kpa 81.7   overrides ambient pressure if the BMP280 log is missing
```

The transducers are **gauge** sensors (0 kPa = atmosphere) and cannot read vacuum, so the intake-stroke pressure
can't be measured directly. Instead the script uses each cylinder's **peak motored compression pressure**:

```
pressure ratio  PR = P_peak_abs / P_ambient
VE estimate        = PR / CR^n          (= 1.0 for a full charge at ambient pressure)
```

Channels averaging below 0.3 V (unplugged / floating) are skipped automatically and noted on the heatmap.

---

## Hardware

#### Data acquisition
- **LabJack T7 Pro**: https://labjack.com/products/labjack-t7-pro
    - Reads the four in-cylinder pressure transducers on analog inputs AIN0-AIN3 (±10 V range, 15 kHz per channel)
    - Reads the crankshaft position signal (from the LM1815) on digital input FIO0, streamed as part of `FIO_STATE`

#### Sensors
We measure pressure inside each cylinder plus ambient pressure and temperature.

In-cylinder:
- 4 × RIFE 200 PSI pressure transducer, 1/8" NPT (gauge, 0.5-4.5 V, ratiometric to its 5 V supply)

Ambient temperature and pressure:
- Adafruit BMP280 I2C or SPI Barometric Pressure & Altitude Sensor (used over I2C at address 0x77)

Engine speed:
- CBR600RR crankshaft position sensor (2-wire variable reluctance) → **LM1815** VR sensor interface → 
  digital pulse per tooth, shared by the ESP32 and the LabJack

#### Microcontroller
- **ESP32-C6-DevKitC-1**
    - Controls the Bosch electronic throttle body on the CBR engine (closed-loop PI + spring feedforward, through a BTS7960 H-bridge)
    - Controls the Predator engine's throttle cable with a servo (holds a target RPM during sweeps)
    - Reads engine speed from the crankshaft position sensor
    - Reads ambient temperature and pressure (BMP280, I2C) once per second during runs
    - Planned: read the 5 V bus to catch dips that would affect the ratiometric pressure transducers 

#### Actuators and power
- Bosch 32 mm electronic throttle body (0280.750.148): 12 V motor, two redundant position sensors (TPS1 rises, TPS2 falls as it opens)
- BTS7960 (IBT-2) H-bridge motor driver
- ZOSKAY 40 kg servo on the Predator throttle
- 5 V / 12 V bench supply, star-point grounding, key switch + E-stop + start button 

---

## Firmware (ESP32-C6)

### Pin assignments

| GPIO | Function |
|---|---|
| 4 | Crank signal in (LM1815 output, falling edge = tooth, interrupt) |
| 5 | Predator throttle servo PWM (50 Hz, 500-2500 µs) |
| 10 / 11 | H-bridge RPWM / LPWM |
| 22 / 23 | H-bridge R_EN / L_EN |
| 2 / 3 | TPS1 / TPS2 (ADC, through 10k/15k dividers) |
| 18 / 19 | I2C SDA / SCL (BMP280) |

### Serial protocol (115200 baud)
Lines starting with `#` are human-readable status/errors; data lines are `time_us,TYPE,value`.
At boot the firmware prints `# READY throttle_calibrated=<0|1> ambient=<0|1>`.

| Command | Action |
|---|---|
| `START` / `STOP` | start / stop logging |
| `SPEED <0-180>` | Predator servo angle |
| `THROTTLE <0-100>` | Bosch throttle target %, starts closed-loop control |
| `MARK <n>` | log a step marker (used by the sweep) |
| `CAL` | calibrate the throttle body (plate moves) |
| `RESET` | clear a latched throttle fault |


## Wiring diagram

`data/wiring.yaml` is a [WireViz](https://github.com/wireviz/WireViz) file and the source of truth for pins and wiring.
From the `TeamAutoflow` folder:

```bash
uv run wireviz data/wiring.yaml        # writes wiring.svg / .png / .html / .bom.tsv next to it
```

`uv run wireviz` needs WireViz in the project (`uv add --dev wireviz`) and the **Graphviz** program installed
(Linux: `sudo apt install graphviz`; Windows: installer from https://graphviz.org/download/, tick
"add Graphviz to the system PATH").

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "LabJack stream failed to start after 3 tries" | Real error is in the run's `meta.json` → `"retries"`. Usually another program has the T7 open, or a stream was left running. |
| T7 **STATUS** LED blinking continuously | A stream is still active on the T7. Close other programs (`ps aux \| grep -i -E "python\|kipling"`), or unplug the T7's USB **and** Ethernet for 5 s. |
| "ESP32 not found" | Check the USB cable, `dialout` group membership, and that no serial monitor has the port open. |
| Serial not working but LED is on ESP32 | Check for header pin over J5 on ESP32. Those pins must be jumped for chip to get power. |
| Ambient temp/pressure show `--` | BMP280 didn't start at boot (`ambient=0` in the READY line). Check wiring at the sensor (VIN, GND, SDA, SCL), then power-cycle the ESP32. |
| A pressure channel reads negative (e.g. −0.39 V) | That input is floating: signal wire not reaching the T7 or power not reaching sensor. Swap channels to see if it follows the sensor, then meter the sensor output and wire. |
| Throttle shows `--` | Throttle not calibrated, or control not on yet (firmware only logs `pos` while control is active). |
| `# FAULT TPS sensors disagree...` | Throttle body connector/wiring or a worn sensor track. Fix, then send `RESET`. |
| Noisy pressure readings | Twist each transducer's wires, keep them away from motor/servo/starter wiring, consider differential inputs on the T7 and a USB isolator on the ESP32 (ground loop). |

### Windows-specific

| Symptom | Likely cause / fix |
|---|---|
| `uv` not recognized after installing | Close and reopen the terminal (the installer updates PATH for new windows only). |
| No COM port appears for the ESP32 | Missing USB-serial driver: install the Silicon Labs **CP210x** (or WCH **CH343**) driver for the chip on the board's UART port, or use the board's native **USB** port (no driver needed). Try a different cable; many USB-C cables are charge-only. |
| `could not open port 'COMx': PermissionError / Access is denied` | Another program has the port open (Arduino IDE serial monitor, `arduino-cli monitor`, a second copy of the GUI). Windows only allows one program per COM port. |
| COM port number changed | Windows assigns a new number per physical USB port. Check Device Manager → Ports, or `arduino-cli board list`. The GUI finds the ESP32 automatically; only `arduino-cli upload -p` needs the number. |
| LabJack not found (`LJME_NO_DEVICES_FOUND`) | LJM not installed, or Kipling has the T7 open. Close Kipling; reinstall LJM if needed. |
| T7 found over USB but not Ethernet | Windows Firewall blocking LJM, or the PC and T7 on different subnets. USB is simpler and fast enough for this bench. |
| GUI is huge or cut off | Windows display scaling (e.g. 150 %) stacks with `UI_SCALE` in `main_gui.py`. Lower `UI_SCALE` (e.g. 1.0) on high-DPI laptops. |
| Leftover Python process holding the T7 | `tasklist \| findstr python`, then `taskkill /F /PID <pid>` (or end it in Task Manager). |
| `arduino-cli` / `wireviz` / `dot` not recognized | The program's folder isn't on PATH: add it under *Edit the system environment variables → Environment Variables → Path*, then open a new terminal. |
