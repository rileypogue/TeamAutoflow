# main graphical user interface using python (tkinter)
# authors: Riley Pogue
# last edited: 11/16/2026 (run folders, throttle control, auto sweep)

import customtkinter as ctk
import serial
from serial.tools import list_ports
import os
import csv
import subprocess
import sys
import threading
import time
from tkinter import filedialog
from pathvalidate import validate_filename, ValidationError
from datetime import datetime
from zoneinfo import ZoneInfo
from DAQ import LJLogger

BAUD_RATE = 115200
ESP_VIDS = {0x10C4, 0x1A86, 0x0403, 0x303A}   # CP210x, CH340/CH343, FTDI, Espressif native USB
DATA_DIR = "../data"

# ---- sweep / RPM-hold tuning (adjust on the bench) ----
RPM_TOL = 40            # rpm band that counts as "at speed"
THR_TOL = 0.02          # throttle plate band (0..1) that counts as "at position"
SETTLE_S = 1.5          # must stay inside both bands this long before the dwell starts
STEP_TIMEOUT_S = 60     # give up settling after this long (step is flagged settled=0)
TICK_MS = 250           # sweep / RPM-hold update period
RPM_KI = 0.01           # servo degrees per (rpm error * second). Lower if RPM oscillates, raise if too slow.
RPM_SLEW = 10.0         # max servo degrees per second
ANGLE_MAX = 180.0       # LOWER THIS to the highest servo angle that is safe for the engine/dyno
RPM_ABORT_MARGIN = 1.25 # abort the sweep if RPM exceeds 125% of the highest setpoint

SWEEP_TOP_GAP = 90   # empty space above the "Auto sweep" section: raise to push it farther down the page
FONT_SIZE = 18       # text size everywhere (customtkinter default is 13). Changes text only, not widget sizes.
UI_SCALE = 1.25   # scales text AND widget sizes together: 1.0 = original, 1.5 = much bigger

ctk.set_appearance_mode("Dark")
ctk.set_widget_scaling(UI_SCALE)
ctk.set_default_color_theme("blue")

# ---- school colors ----
ACCENT = "#D2492A"        # Colorado red
ACCENT_HOVER = "#B03B20"  # darker red for hover
DARK_BLUE = "#21314D"
SILVER = "#81848A"
WHITE = "#FFFFFF"
HEADER_BG = "#17233A"     # darker shade of the school blue
PANEL_BG = "#2B3F63"      # lighter shade of the school blue
FIELD_BG = "#17233A"
MUTED = SILVER

START_FG, START_HOVER, START_TEXT = ACCENT, ACCENT_HOVER, WHITE
STOP_FG, STOP_HOVER, STOP_TEXT = WHITE, "#D9DCE1", DARK_BLUE
ANALYZE_FG, ANALYZE_HOVER = SILVER, "#6B6E74"   # analyze button: school silver, darker on hover

# default widget colors (applied to every widget created after this point)
_theme = ctk.ThemeManager.theme
_theme["CTkButton"].update(fg_color=[ACCENT, ACCENT], hover_color=[ACCENT_HOVER, ACCENT_HOVER],
                           text_color=[WHITE, WHITE])
_theme["CTkEntry"].update(fg_color=[FIELD_BG, FIELD_BG], border_color=[SILVER, SILVER],
                          text_color=[WHITE, WHITE], placeholder_text_color=[SILVER, SILVER])
_theme["CTkSwitch"].update(progress_color=[ACCENT, ACCENT], button_color=[WHITE, WHITE],
                           button_hover_color=[SILVER, SILVER], fg_color=[SILVER, SILVER],
                           text_color=[WHITE, WHITE])
_theme["CTkLabel"].update(text_color=[WHITE, WHITE])
if "CTkFont" in _theme:
    _theme["CTkFont"]["size"] = FONT_SIZE   # default font for every widget that does not set its own
# LUT values for pressure transducer: voltage vs kPa
PRESSURE_VOLTAGES = [0.5, 0.629, 0.758, 0.887, 1.016, 1.145, 1.274, 1.403, 1.532, 1.661, 1.790, 1.919, 2.048, 2.177, 2.306, 2.435, 2.564, 2.693, 2.822, 2.951, 3.080, 3.209, 3.338, 3.467, 3.596, 3.725, 3.854, 3.983, 4.112, 4.241, 4.370, 4.50]
PRESSURE_VALUES = [0.0, 44.5, 89.0, 133.5, 177.9, 222.4, 266.9, 311.4, 355.9, 400.4, 444.8, 489.3, 533.8, 578.3, 622.8, 667.3, 711.7, 756.2, 800.7, 845.2, 889.7, 934.2, 978.6, 1023.1, 1067.6, 1112.1, 1156.6, 1201.1, 1245.5, 1290.0, 1334.5, 1379.0]


def parse_list(text):
    """'1500,2000,2500' or '1000:3000:500' (start:stop:step, inclusive) -> list of floats."""
    out = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if ":" in part:
            a, b, s = (float(x) for x in part.split(":"))
            if s <= 0:
                raise ValueError("range step must be > 0")
            v = a
            while v <= b + 1e-9:
                out.append(v)
                v += s
        else:
            out.append(float(part))
    return out


def open_esp32(baud=115200, probe_s=4.0):
    """Find the ESP32 on any serial port (/dev/ttyUSB*, COM*, ...). Opening the port resets the
    ESP32, so we look for the "# READY" line its firmware prints at boot. Returns an open Serial or None."""
    ports = sorted(list_ports.comports(), key=lambda p: p.vid not in ESP_VIDS)  # likely ESP32s first
    fallback = None
    for p in ports:
        try:
            s = serial.Serial(p.device, baud, timeout=0.2, write_timeout=1)
        except (serial.SerialException, OSError):
            continue
        t0 = time.time()
        while time.time() - t0 < probe_s:
            if s.readline().decode("utf-8", errors="replace").startswith("# READY"):
                s.timeout = 1
                return s
        s.close()
        if fallback is None and p.vid in ESP_VIDS:
            fallback = p.device
    if fallback:                       # board did not announce itself (e.g. no auto-reset): trust the USB chip ID
        try:
            s = serial.Serial(fallback, baud, timeout=1, write_timeout=1)
            time.sleep(2.5)            # let the ESP32 finish resetting
            return s
        except (serial.SerialException, OSError):
            pass
    return None


class App(ctk.CTk):
    def __init__(self):
        super().__init__() # call __init__ method from original CTkinter class

        self.geometry("1000x780")
        self.title("Flowbench")
        self.configure(fg_color=DARK_BLUE)
        self.is_running = False
        self.ser = None
        self.job = None
        self.csv = None
        self.writer = None
        self.run_name = None
        self.run_dir = None
        self.angle = 0.0            # last servo angle we commanded
        self.latest = {}            # newest value per log type from the ESP32
        self.latest_t = {}          # PC time each of those arrived
        self.sweep = None
        self.sweep_job = None
        self.steps_file = None
        self.steps_writer = None
        self.tare_dir = None        # last tare folder picked, offered again next time
        self.analysis = None        # background analysis in progress

        self.daq = LJLogger(fs=15000)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # header
        header = ctk.CTkFrame(self, height=80, corner_radius=0, fg_color=HEADER_BG)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkFrame(header, height=4, corner_radius=0, fg_color=ACCENT).pack(side="bottom", fill="x")  # red accent line
        ctk.CTkLabel(header, text="Flowbench", font=ctk.CTkFont(size=FONT_SIZE + 14, weight="bold"),).pack(side="left", padx=(30, 12), pady=20)
        # ctk.CTkLabel(header, text="Super Suck Time!!", text_color=MUTED,).pack(side="left", pady=(8, 0))

        # body
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=30, pady=30)
        body.grid_columnconfigure((0, 1), weight=1, uniform="panels")
        body.grid_rowconfigure(0, weight=1)

        # left panel: manual controls + sweep
        # scrollable, so nothing is cut off on a short screen when the UI is scaled up
        left = getattr(ctk, "CTkScrollableFrame", ctk.CTkFrame)(body, corner_radius=16, fg_color=PANEL_BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.entry = ctk.CTkEntry(left, height=45, corner_radius=10, placeholder_text="Servo angle 0-180 (manual)")
        self.entry.pack(fill="x", padx=24, pady=(16, 10))
        self.entry.bind("<Return>", lambda event: self.send_value())
        ctk.CTkButton(left, text="Send", width=100, height=36, command=self.send_value).pack(anchor="e", padx=24)

        self.thr_entry = ctk.CTkEntry(left, height=45, corner_radius=10, placeholder_text="Throttle plate 0-100 % (manual)")
        self.thr_entry.pack(fill="x", padx=24, pady=(14, 10))
        self.thr_entry.bind("<Return>", lambda event: self.send_throttle())
        ctk.CTkButton(left, text="Send", width=100, height=36, command=self.send_throttle).pack(anchor="e", padx=24)

        self.feedback = ctk.CTkLabel(left, text="", text_color=MUTED, wraplength=380, justify="left")
        self.feedback.pack(anchor="w", padx=24, pady=(12, 0))

        ctk.CTkLabel(left, text="Auto sweep", font=ctk.CTkFont(size=FONT_SIZE + 4, weight="bold")).pack(anchor="w", padx=24, pady=(SWEEP_TOP_GAP, 4))
        self.sweep_switch = ctk.CTkSwitch(left, text="Run sweep when START is pressed",
                                          switch_width=72, switch_height=36)   # default is 36 x 18
        self.sweep_switch.pack(anchor="w", padx=24, pady=(4, 12))
        self.rpm_list_entry = ctk.CTkEntry(left, height=36, corner_radius=10,
                                           placeholder_text="RPM setpoints: 1500,2000 or 1000:3000:500")
        self.rpm_list_entry.pack(fill="x", padx=24, pady=(0, 6))
        self.thr_list_entry = ctk.CTkEntry(left, height=36, corner_radius=10,
                                           placeholder_text="Throttle %: 10,20,40,60,100 (blank = cable/manual)")
        self.thr_list_entry.pack(fill="x", padx=24, pady=(0, 6))
        self.dwell_entry = ctk.CTkEntry(left, height=36, corner_radius=10, placeholder_text="Dwell seconds per point (default 5)")
        self.dwell_entry.pack(fill="x", padx=24, pady=(0, 6))
        self.sweep_status = ctk.CTkLabel(left, text="", text_color=MUTED, wraplength=380, justify="left")
        self.sweep_status.pack(anchor="w", padx=24, pady=(6, 0))

        # right panel: file name + start/stop
        right = ctk.CTkFrame(body, corner_radius=16, fg_color=PANEL_BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        self.name_entry = ctk.CTkEntry(right, height=45, corner_radius=10, placeholder_text="Run name (a folder is made per run)")
        self.name_entry.pack(fill="x", padx=24, pady=(16, 10))
        self.name_entry.bind("<Return>", lambda event: self.send_name())
        ctk.CTkButton(right, text="Send", width=100, height=36, command=self.send_name).pack(anchor="e", padx=24)
        self.name_feedback = ctk.CTkLabel(right, text="", text_color=MUTED, wraplength=380, justify="left")
        self.name_feedback.pack(anchor="w", padx=24, pady=(12, 0))

        # live readout of what the ESP32 is sending (only streams while recording)
        self.live_label = ctk.CTkLabel(right, text="", text_color=MUTED, justify="left", anchor="w")
        self.live_label.pack(anchor="w", padx=24, pady=(12, 0))

        ctk.CTkButton(right, text="Reconnect ESP32", height=36, fg_color="transparent", border_width=2,
                      border_color=SILVER, hover_color=FIELD_BG, command=self.connect_serial).pack(fill="x", padx=24, pady=(10, 0))

        self.analyze_button = ctk.CTkButton(right, text="Analyze a run...", height=50, fg_color=ANALYZE_FG,
                                            hover_color=ANALYZE_HOVER, text_color=WHITE,
                                            font=ctk.CTkFont(size=FONT_SIZE + 2, weight="bold"), command=self.analyze_run)
        self.analyze_button.pack(fill="x", padx=24, pady=(6, 14))

        self.toggle_button = ctk.CTkButton(right, width=300, height=50, fg_color=START_FG, hover_color=START_HOVER, text="START",
                                           font=ctk.CTkFont(size=FONT_SIZE + 4, weight="bold"), command=self.toggle_state)
        self.toggle_button.pack(expand=True, fill="x", padx=24)

        self.toggle_button.configure(state="disabled")
        self.bind("<F11>", lambda e: self.attributes("-fullscreen", not self.attributes("-fullscreen")))
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))
        self.poll_status()
        self.update_live()
        self.after(200, self.maximize)
        self.after(500, self.connect_serial)   # after the window is up, so the port search doesn't freeze an empty screen

    def maximize(self):
        try:
            if sys.platform.startswith("win"):
                self.state("zoomed")                 # Windows
            else:
                self.attributes("-zoomed", True)     # Linux (X11)
        except Exception:
            self.attributes("-fullscreen", True)     # fallback, e.g. some Wayland setups

    def connect_serial(self):
        if self.is_running:
            return
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.toggle_button.configure(state="disabled")
        self.feedback.configure(text="Searching for ESP32...", text_color=MUTED)
        self.update_idletasks()
        self.ser = open_esp32(BAUD_RATE)
        if self.ser:
            self.feedback.configure(text=f"ESP32 connected on {self.ser.port}", text_color=MUTED)
            self.toggle_button.configure(state="normal")
        else:
            self.feedback.configure(text="ESP32 not found - plug it in and press Reconnect ESP32", text_color="#ff5c5c")

    def update_live(self):
        """Twice a second, show the newest values received. '--' means nothing arrived recently."""
        if not self.is_running:
            self.live_label.configure(text="Live readings appear here while recording", text_color=MUTED)
        else:
            def show(key, max_age, fmt):
                v = self.fresh(key, max_age)
                return "--" if v is None else fmt.format(v)
            pos = self.fresh("pos", 1.0)
            lines = [
                "RPM:  " + show("RPM", 1.5, "{:.0f}"),
                "Throttle plate:  " + ("--" if pos is None else f"{pos * 100:.0f} %"),
                "Ambient temp:  " + show("amb_T", 12.0, "{:.1f} C"),
                "Ambient pressure:  " + show("amb_P", 12.0, "{:.2f} kPa"),
            ]
            ls = self.daq.last_scan
            if ls is not None:
                lines += [f"AIN{i}:  {ls[i]:.3f} V" for i in range(4)]
            missing = self.fresh("amb_T", 12.0) is None or self.fresh("amb_P", 12.0) is None
            self.live_label.configure(text="\n".join(lines), text_color="#ffb347" if missing else WHITE)
        self.after(500, self.update_live)

    # ---------------- serial helpers ----------------
    def send(self, text):
        try:
            self.ser.write((text + "\n").encode())
            return True
        except (serial.SerialException, AttributeError, OSError) as e:
            print(f"Send failed ({text}): {e}")
            return False

    def fresh(self, key, max_age):
        """Newest logged value of this type if it arrived within max_age seconds, else None."""
        t = self.latest_t.get(key)
        if t is None or time.time() - t > max_age:
            return None
        return self.latest[key]

    def poll_status(self):
        """While not recording, show '#' status lines from the ESP32 (calibration results, errors)."""
        if self.ser and not self.is_running and self.csv is None:
            try:
                while self.ser.in_waiting > 0:
                    line = self.ser.readline().decode("utf-8", errors="replace").strip()
                    if line.startswith("#"):
                        print(line)
                        self.feedback.configure(text=line[1:].strip(), text_color=MUTED)
            except (serial.SerialException, OSError):
                pass
        self.after(100, self.poll_status)

    def drain_serial(self):
        while self.ser.in_waiting > 0:
            line = self.ser.readline().decode("utf-8", errors="replace").strip()
            if line.startswith("#"):
                print(line)
                self.feedback.configure(text=line[1:].strip(), text_color=MUTED)
                if "FAULT" in line and self.sweep is not None:
                    self.stop_sweep("Aborted: throttle fault", stop_recording=True)
                continue
            parts = line.split(",")
            if len(parts) == 3:
                if self.writer:
                    self.writer.writerow(parts)
                try:
                    self.latest[parts[1]] = float(parts[2])
                    self.latest_t[parts[1]] = time.time()
                except ValueError:
                    pass
        if self.csv:
            self.csv.flush()

    def poll_serial(self):
        if not self.is_running:
            return
        try:
            self.drain_serial()
        except (serial.SerialException, OSError) as e:
            print(f"Serial read failed: {e}")
            self.stop_sweep("Aborted: serial lost")
            self.is_running = False
            self.end_recording()
            self.reset_button()
            return

        self.job = self.after(50, self.poll_serial) # run again in 50 ms

    # ---------------- start / stop ----------------
    def toggle_state(self):
        self.is_running = not self.is_running
        if self.is_running:
            self.toggle_button.configure(text="STOP", fg_color=STOP_FG, hover_color=STOP_HOVER, text_color=STOP_TEXT)
            self.start_recording()
        else:
            self.toggle_button.configure(text="START", fg_color=START_FG, hover_color=START_HOVER, text_color=START_TEXT)
            self.end_recording()

    def start_recording(self):
        if not self.ser:
            self.feedback.configure(text="ESP32 not connected", text_color="#ff5c5c")
            self.reset_button()
            return

        cfg = None
        if self.sweep_switch.get():          # validate sweep settings before touching anything
            try:
                cfg = self.read_sweep_settings()
            except ValueError as e:
                self.sweep_status.configure(text=f"Sweep settings: {e}", text_color="#ff5c5c")
                self.reset_button()
                return

        stamp = datetime.now(ZoneInfo("America/Denver")).strftime("%m-%d_%H-%M-%S")
        self.run_dir = os.path.join(DATA_DIR, f"{self.run_name or 'run'}_{stamp}")
        os.makedirs(self.run_dir, exist_ok=True)
        self.latest.clear()
        self.latest_t.clear()

        try:
            self.ser.reset_input_buffer()
            t_sent = time.time()
            self.ser.write(b"START\n")
        except (serial.SerialException, AttributeError, OSError) as e:
            print(f"ERROR: Could not write to the ESP32: {e}")
            self.reset_button()
            return

        self.csv = open(os.path.join(self.run_dir, "esp.csv"), mode='w', newline='', encoding='utf-8')
        self.writer = csv.writer(self.csv)
        self.writer.writerow(["esp_us_time", "type", "value"])

        self.daq.start(outdir=self.run_dir, minutes=None,
                       meta={"pc_start_sent": t_sent, "run_name": self.run_name, "sweep": cfg})
        self.after(1500, self.check_daq)

        print("run folder: ", self.run_dir)
        self.poll_serial()
        if cfg:
            self.begin_sweep(cfg)

    def check_daq(self):
        errs = self.daq.info.get("errors")
        if self.is_running and errs:
            self.feedback.configure(text=errs[-1], text_color="#ff5c5c")

    def end_recording(self):
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        self.stop_sweep("Sweep stopped")
        self.send("STOP")
        self.toggle_button.configure(state="disabled")
        self.after(200, self.finish_recording)
        self.daq.stop()
        errs = self.daq.info.get("errors")
        if errs:
            self.feedback.configure(text=errs[-1], text_color="#ff5c5c")

    def finish_recording(self):
        if self.csv:
            try:
                self.drain_serial()
            except (serial.SerialException, AttributeError, OSError):
                pass
            self.csv.close()
            self.csv = None
            self.writer = None
            self.name_feedback.configure(text=f"Saved to {self.run_dir}")
        self.toggle_button.configure(state="normal")

    # ---------------- sweep ----------------
    def read_sweep_settings(self):
        rpms = parse_list(self.rpm_list_entry.get())
        if not rpms:
            raise ValueError("enter at least one RPM setpoint")
        if min(rpms) <= 0:
            raise ValueError("RPM setpoints must be > 0")
        thr_txt = self.thr_list_entry.get().strip()
        thrs = parse_list(thr_txt) if thr_txt else [None]
        if any(t is not None and not 0 <= t <= 100 for t in thrs):
            raise ValueError("throttle must be 0-100 %")
        dwell_txt = self.dwell_entry.get().strip()
        dwell = float(dwell_txt) if dwell_txt else 5.0
        if dwell <= 0:
            raise ValueError("dwell must be > 0")
        return {"rpms": rpms, "throttles": thrs, "dwell_s": dwell}

    def begin_sweep(self, cfg):
        steps = []
        for i, rpm in enumerate(cfg["rpms"]):
            thrs = cfg["throttles"] if i % 2 == 0 else cfg["throttles"][::-1]  # serpentine: less plate travel
            for thr in thrs:
                steps.append((rpm, thr))
        self.steps_file = open(os.path.join(self.run_dir, "steps.csv"), "w", newline="")
        self.steps_writer = csv.writer(self.steps_file)
        self.steps_writer.writerow(["step", "rpm_target", "throttle_pct", "settled", "pc_time_mark", "rpm_at_mark", "pos_at_mark"])
        self.sweep = {
            "cfg": cfg, "steps": steps, "i": -1, "phase": "settle",
            "angle": self.angle, "last_tick": time.time(),
            "abort_rpm": max(cfg["rpms"]) * RPM_ABORT_MARGIN,
        }
        self.next_step()
        self.sweep_job = self.after(TICK_MS, self.sweep_tick)

    def next_step(self):
        s = self.sweep
        s["i"] += 1
        if s["i"] >= len(s["steps"]):
            self.stop_sweep("Sweep complete", stop_recording=True)
            return False
        rpm, thr = s["steps"][s["i"]]
        s["phase"] = "settle"
        s["t_step"] = time.time()
        s["t_ok"] = None
        if thr is not None:
            self.send(f"THROTTLE {thr:g}")
        thr_txt = "manual" if thr is None else f"{thr:g} %"
        self.sweep_status.configure(text=f"Step {s['i'] + 1}/{len(s['steps'])}: {rpm:g} RPM, throttle {thr_txt} - settling",
                                    text_color=MUTED)
        return True

    def set_angle(self, angle):
        self.angle = angle
        self.send(f"SPEED {angle:.1f}")

    def sweep_tick(self):
        s = self.sweep
        if s is None or not self.is_running:
            return
        now = time.time()
        dt = now - s["last_tick"]
        s["last_tick"] = now
        rpm_target, thr = s["steps"][s["i"]]
        rpm = self.fresh("RPM", 1.5)

        # safety checks
        if rpm is not None and rpm > s["abort_rpm"]:
            self.stop_sweep(f"Aborted: RPM {rpm:.0f} above limit", stop_recording=True)
            return
        if rpm is None and now - s["t_step"] > 5:
            self.stop_sweep("Aborted: no RPM data from ESP32", stop_recording=True)
            return
        pos = self.fresh("pos", 1.0)
        if thr is not None and pos is None and now - s["t_step"] > 5:
            self.stop_sweep("Aborted: no throttle position feedback (calibrate the throttle first)", stop_recording=True)
            return

        # RPM hold: slow integral controller on servo angle
        if rpm is not None:
            err = rpm_target - rpm
            step = max(-RPM_SLEW * dt, min(RPM_SLEW * dt, RPM_KI * err * dt))
            s["angle"] = max(0.0, min(ANGLE_MAX, s["angle"] + step))
            self.set_angle(s["angle"])

        if s["phase"] == "settle":
            in_band = rpm is not None and abs(rpm_target - rpm) <= RPM_TOL
            if thr is not None:
                in_band = in_band and pos is not None and abs(thr / 100.0 - pos) <= THR_TOL
            if in_band:
                if s["t_ok"] is None:
                    s["t_ok"] = now
                ok = (now - s["t_ok"]) >= SETTLE_S
            else:
                s["t_ok"] = None
                ok = False
            if ok or (now - s["t_step"]) > STEP_TIMEOUT_S:
                s["phase"] = "dwell"
                s["t_dwell_end"] = now + s["cfg"]["dwell_s"]
                self.send(f"MARK {s['i'] + 1}")
                self.steps_writer.writerow([s["i"] + 1, rpm_target, "" if thr is None else thr, int(ok),
                                            f"{now:.3f}", "" if rpm is None else f"{rpm:.0f}",
                                            "" if pos is None else f"{pos:.3f}"])
                self.steps_file.flush()
                self.sweep_status.configure(
                    text=f"Step {s['i'] + 1}/{len(s['steps'])}: recording dwell" + ("" if ok else " (did NOT settle)"),
                    text_color=MUTED if ok else "#ffb347")
        elif s["phase"] == "dwell":
            if now >= s["t_dwell_end"]:
                self.send("MARK 0")
                if not self.next_step():
                    return

        self.sweep_job = self.after(TICK_MS, self.sweep_tick)

    def stop_sweep(self, message, stop_recording=False):
        had = self.sweep is not None
        uses_thr = had and any(t is not None for _, t in self.sweep["steps"])
        if self.sweep_job:
            self.after_cancel(self.sweep_job)
            self.sweep_job = None
        self.sweep = None
        if self.steps_file:
            self.steps_file.close()
            self.steps_file = None
        if had:
            self.send("SPEED 0")
            self.angle = 0.0
            if uses_thr:
                self.send("THROTTLE 0")
            self.sweep_status.configure(text=message)
        if stop_recording and self.is_running:
            self.toggle_state()

    # ---------------- analysis ----------------
    def analyze_run(self):
        """Pick a run folder (and optionally a tare folder), run analyze_run.py on it, then open the VE graph."""
        if self.is_running or self.csv is not None:
            self.name_feedback.configure(text="Stop recording before analyzing", text_color="#ff5c5c")
            return
        if self.analysis is not None:
            return
        data_dir = os.path.abspath(DATA_DIR)
        start = data_dir if os.path.isdir(data_dir) else os.getcwd()
        run = filedialog.askdirectory(parent=self, title="Pick the RUN folder to analyze", initialdir=start)
        if not run:
            return
        if not os.path.exists(os.path.join(run, "lj.bin")):
            self.name_feedback.configure(text="That is not a run folder (no lj.bin inside)", text_color="#ff5c5c")
            return
        tare = filedialog.askdirectory(parent=self, title="Pick the TARE folder (Cancel = analyze without a tare)",
                                       initialdir=self.tare_dir or os.path.dirname(run))
        if tare and not os.path.exists(os.path.join(tare, "lj.bin")):
            self.name_feedback.configure(text="That tare folder has no lj.bin inside", text_color="#ff5c5c")
            return
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyze_run.py")
        cmd = [sys.executable, script, run]
        if tare:
            self.tare_dir = tare
            cmd += ["--tare", tare]

        job = {"run": run, "tare": bool(tare), "result": None}

        def work():   # separate process + thread, so the window stays responsive
            try:
                job["result"] = subprocess.run(cmd, capture_output=True, text=True)
            except Exception as e:
                job["result"] = e

        self.analysis = job
        self.analyze_button.configure(state="disabled")
        self.name_feedback.configure(text=f"Analyzing {os.path.basename(run)}...", text_color=MUTED)
        threading.Thread(target=work, daemon=True).start()
        self.after(300, self.check_analysis)

    def check_analysis(self):
        job = self.analysis
        if job is None:
            return
        res = job["result"]
        if res is None:
            self.after(300, self.check_analysis)
            return
        self.analysis = None
        self.analyze_button.configure(state="normal")
        if isinstance(res, Exception):
            self.name_feedback.configure(text=f"Could not start analysis: {res}", text_color="#ff5c5c")
            return
        print(res.stdout)
        if res.returncode != 0:
            print(res.stderr)
            lines = [ln for ln in (res.stderr or res.stdout).strip().splitlines() if ln.strip()]
            self.name_feedback.configure(text="Analysis failed: " + (lines[-1] if lines else "unknown error"),
                                         text_color="#ff5c5c")
            return
        note = "" if job["tare"] else " (no tare: expect a zero-offset error)"
        self.name_feedback.configure(text=f"Analysis saved in {job['run']}{note}", text_color=MUTED)
        self.open_file(os.path.join(job["run"], "ve_heatmap.png"))

    def open_file(self, path):
        """Open a file in the computer's default viewer."""
        if not os.path.exists(path):
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            print(f"Could not open {path}: {e}")

    # ---------------- manual controls ----------------
    def send_name(self):
        text = os.path.splitext(self.name_entry.get().strip())[0]
        try:
            validate_filename(text)
        except ValidationError as e:
            self.name_feedback.configure(text=f"{text} is not a valid name: {e}", text_color="#ff5c5c")
            return
        self.run_name = text
        self.name_feedback.configure(text=f"Runs will be saved in {DATA_DIR}/{text}_<time>/", text_color=MUTED)

    def send_value(self):
        if self.sweep is not None:
            self.feedback.configure(text="Sweep is running - stop it first", text_color="#ff5c5c")
            return
        text = self.entry.get().strip()
        try:
            value = int(text)
        except ValueError:
            self.feedback.configure(text="Please enter whole number... ", text_color="#ff5c5c")
            return
        if not 0 <= value <= 180:
            self.feedback.configure(text="Number should be between 0 and 180", text_color="#ff5c5c")
            return
        if not self.send(f"SPEED {value}"):
            self.feedback.configure(text="Did not send speed, ESP32 not connected", text_color="#ff5c5c")
            return
        self.angle = float(value)
        self.feedback.configure(text=f"Servo angle: {value}", text_color=MUTED)
        self.entry.delete(0, "end") # clear text entry box

    def send_throttle(self):
        if self.sweep is not None:
            self.feedback.configure(text="Sweep is running - stop it first", text_color="#ff5c5c")
            return
        text = self.thr_entry.get().strip()
        try:
            value = float(text)
        except ValueError:
            self.feedback.configure(text="Please enter a number 0-100", text_color="#ff5c5c")
            return
        if not 0 <= value <= 100:
            self.feedback.configure(text="Throttle should be between 0 and 100 %", text_color="#ff5c5c")
            return
        if not self.send(f"THROTTLE {value:g}"):
            self.feedback.configure(text="Did not send throttle, ESP32 not connected", text_color="#ff5c5c")
            return
        self.feedback.configure(text=f"Throttle: {value:g} %", text_color=MUTED)
        self.thr_entry.delete(0, "end")

    def on_close(self):
        try:
            self.stop_sweep("")
            if self.ser and self.ser.is_open:
                self.ser.write(b"SPEED 0\n") # return throttle to 0
                if self.is_running:
                    self.ser.write(b"STOP\n")
                self.ser.close()
        except (serial.SerialException, AttributeError, OSError):
            pass
        finally:
            if self.job:
                self.after_cancel(self.job)
            if self.daq.running:
                self.daq.stop()
            if self.csv:
                self.csv.close()
            self.destroy()

    def reset_button(self):
        self.is_running = False
        self.toggle_button.configure(text="START", state="normal", fg_color=START_FG, hover_color=START_HOVER, text_color=START_TEXT)


if __name__ == "__main__":
    app = App()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.on_close()
