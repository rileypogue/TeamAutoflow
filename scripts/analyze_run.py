#!/usr/bin/env python3

import argparse
import csv
import json
import os
import sys

import numpy as np
from scipy.signal import find_peaks

import matplotlib
matplotlib.use("Agg")          # no window: the GUI opens the saved PNG
import matplotlib.pyplot as plt

# config 
PRESSURE_CHANNELS = ["AIN0", "AIN1", "AIN2", "AIN3"]
CYL_NAMES = ["cyl1", "cyl2", "cyl3", "cyl4"]   # cylinder plumbed to each channel above
CKP_CHANNEL = "FIO_STATE"
CKP_BIT = 0                     # FIO0 = bit 0 of FIO_STATE
PULSES_PER_REV = 12             # LM1815 pulses per crank rev
COMPRESSION_RATIO = 12.2        # CBR600RR geometric CR
POLYTROPIC_N = 1.30             # motored compression exponent (assumption, ~1.25-1.35)
DEAD_CHANNEL_V = 0.30           # mean volts below this = sensor unplugged/floating, ignored
MIN_PEAK_PROMINENCE_KPA = 20.0  # smallest compression bump that counts as a cycle
MANUAL_WINDOW_S = 1.0           # slice length for runs without steps.csv
RPM_BIN = 250                   # heatmap bins for manual runs
THR_BIN = 10

# MAP sensor (absolute, plumbed to the intake plenum downstream of the throttle plate) on AIN4.
# EDIT the two calibration points from your sensor's datasheet (linear: volts -> kPa absolute).
MAP_CHANNEL = "AIN4"
MAP_CAL_V = (0.5, 4.5)
MAP_CAL_KPA = (10.0, 105.0)
MAP_BREAKPOINTS = [20, 30, 40, 50, 60, 70, 80]   # kPa abs rows of the VE table (ambient in Golden ~83 kPa)

# pressure transducer LUT taken from RIFE sensor website 
PRESSURE_VOLTAGES = [0.5, 0.629, 0.758, 0.887, 1.016, 1.145, 1.274, 1.403, 1.532, 1.661, 1.790,
                     1.919, 2.048, 2.177, 2.306, 2.435, 2.564, 2.693, 2.822, 2.951, 3.080, 3.209,
                     3.338, 3.467, 3.596, 3.725, 3.854, 3.983, 4.112, 4.241, 4.370, 4.50]
PRESSURE_VALUES = [0.0, 44.5, 89.0, 133.5, 177.9, 222.4, 266.9, 311.4, 355.9, 400.4, 444.8,
                   489.3, 533.8, 578.3, 622.8, 667.3, 711.7, 756.2, 800.7, 845.2, 889.7, 934.2,
                   978.6, 1023.1, 1067.6, 1112.1, 1156.6, 1201.1, 1245.5, 1290.0, 1334.5, 1379.0]

FULL_CHARGE_PR = COMPRESSION_RATIO ** POLYTROPIC_N

# runs whenever we're missing essential data for analysis
def fail(msg):
    sys.exit(f"ERROR: {msg}")


def volts_to_kpa(v):
    v = np.asarray(v, dtype=float) # convert voltages to NumPy array of decimal values
    lv, lk = np.asarray(PRESSURE_VOLTAGES), np.asarray(PRESSURE_VALUES) # turns voltage and kpa lists into np arrays
    out = np.interp(v, lv, lk)  # linear interp for every voltage value find voltage and pressure values its in between
    lo, hi = v < lv[0], v > lv[-1]
    out[lo] = lk[0] + (v[lo] - lv[0]) * (lk[1] - lk[0]) / (lv[1] - lv[0]) # replaces values below 0.5 V by extending table
    out[hi] = lk[-1] + (v[hi] - lv[-1]) * (lk[-1] - lk[-2]) / (lv[-1] - lv[-2])
    return out

def volts_to_map_kpa(v):
    (v0, v1), (k0, k1) = MAP_CAL_V, MAP_CAL_KPA
    return k0 + (np.asarray(v, dtype=float) - v0) * (k1 - k0) / (v1 - v0)


def load_lj(run_dir):
    meta_path = os.path.join(run_dir, "meta.json")
    bin_path = os.path.join(run_dir, "lj.bin")
    if not os.path.exists(meta_path):
        fail(f"no meta.json in {run_dir}")
    with open(meta_path) as f:
        meta = json.load(f)
    names = meta.get("channels")
    fs = meta.get("actual_fs") or meta.get("requested_fs")
    if not names or not fs:
        fail("meta.json is missing channels or scan rate (did the stream start?)")
    raw = np.fromfile(bin_path, dtype=np.float32)
    n = len(names)
    raw = raw[: (raw.size // n) * n]          # drop a partial last scan if the run was cut off
    if raw.size == 0:
        fail(f"lj.bin in {run_dir} is empty")
    data = raw.reshape(-1, n)
    for ch in PRESSURE_CHANNELS + [CKP_CHANNEL]:
        if ch not in names:
            fail(f"channel {ch} not in this run (channels: {names})")
    return data, names, float(fs), meta


def load_esp(run_dir, meta):
    """{type: (pc_time_array, value_array)}. ESP micros are mapped to PC time by assuming the
    first logged line arrived right after START was sent (good to ~0.1 s, fine for ambient/throttle)."""
    path = os.path.join(run_dir, "esp.csv")
    rows = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.reader(f):
                if len(r) != 3:
                    continue
                try:
                    rows.append((float(r[0]), r[1].strip(), float(r[2])))
                except ValueError:
                    continue               # header or junk line
    out = {}
    if not rows:
        return out
    t_first = rows[0][0]
    pc0 = float(meta.get("pc_start_sent", meta.get("lj_t0", 0.0)))
    for t_us, key, val in rows:
        out.setdefault(key, ([], []))
        out[key][0].append(pc0 + (t_us - t_first) / 1e6)
        out[key][1].append(val)
    return {k: (np.array(t), np.array(v)) for k, (t, v) in out.items()}


def tare_offsets(tare_dir):
    # mean pressure 
    data, names, _, _ = load_lj(tare_dir)
    return {ch: float(np.mean(volts_to_kpa(data[:, names.index(ch)]))) for ch in PRESSURE_CHANNELS}


def esp_value(esp, keys, t0, t1, pad=0.0):
    for k in keys:
        if k in esp:
            t, v = esp[k]
            m = (t >= t0 - pad) & (t <= t1 + pad)
            if m.any():
                return float(np.median(v[m]))
    return float("nan")


def build_windows(run_dir, meta, n_scans, fs, esp):
    """List of dicts: t0, t1 (PC time), rpm_target, throttle, settled."""
    lj_t0 = float(meta.get("lj_t0", meta.get("pc_start_sent", 0.0)))
    t_end = lj_t0 + n_scans / fs
    steps_path = os.path.join(run_dir, "steps.csv")
    sweep = meta.get("sweep") or {}
    windows = []
    if os.path.exists(steps_path):
        dwell = float(sweep.get("dwell_s", 5.0))
        with open(steps_path, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    t0 = float(r["pc_time_mark"])
                except (KeyError, ValueError):
                    continue
                thr = r.get("throttle_pct", "")
                windows.append({
                    "step": int(r["step"]), "t0": t0, "t1": min(t0 + dwell, t_end),
                    "rpm_target": float(r["rpm_target"]),
                    "throttle": float(thr) if thr not in ("", None) else float("nan"),
                    "settled": int(r.get("settled", 1) or 0),
                })
    if windows:
        return windows, "sweep"
    # manual run: fixed slices, throttle from the ESP32 position log if present
    t = lj_t0
    i = 0
    while t + 0.5 * MANUAL_WINDOW_S <= t_end:
        t1 = min(t + MANUAL_WINDOW_S, t_end)
        pos = esp_value(esp, ["pos", "tps1"], t, t1)
        windows.append({"step": i + 1, "t0": t, "t1": t1, "rpm_target": float("nan"),
                        "throttle": pos * 100.0 if pos == pos else float("nan"), "settled": 1})
        t, i = t1, i + 1
    return windows, "manual"


# ---------------------------------------------------------------- per-window analysis
def window_rpm(ckp_bits, fs):
    """RPM from LM1815 falling edges (output pulls LOW for each tooth)."""
    edges = np.flatnonzero((ckp_bits[:-1] == 1) & (ckp_bits[1:] == 0))
    if len(edges) < 3:
        return float("nan"), edges
    span_s = (edges[-1] - edges[0]) / fs
    return 60.0 * (len(edges) - 1) / span_s / PULSES_PER_REV, edges


def cylinder_peaks(p_abs, fs, rpm):
    """Indices of compression peaks: one per 720 deg cycle."""
    if rpm == rpm and rpm > 0:
        distance = max(1, int(0.7 * fs * 120.0 / rpm))   # 70 % of one 4-stroke cycle
    else:
        distance = max(1, int(0.01 * fs))
    span = np.percentile(p_abs, 99) - np.median(p_abs)
    prominence = max(MIN_PEAK_PROMINENCE_KPA, 0.3 * span)
    peaks, _ = find_peaks(p_abs, distance=distance, prominence=prominence)
    return peaks


def analyze(run_dir, tare_dir=None):
    data, names, fs, meta = load_lj(run_dir)
    esp = load_esp(run_dir, meta)
    offsets = tare_offsets(tare_dir) if tare_dir else {ch: 0.0 for ch in PRESSURE_CHANNELS}
    lj_t0 = float(meta.get("lj_t0", meta.get("pc_start_sent", 0.0)))

    # ambient pressure: per-window ESP value, falling back to the whole-run median
    run_amb = None 
    if run_amb is None and "amb_P" in esp:
        run_amb = float(np.median(esp["amb_P"][1]))
    if run_amb is None:
        fail("no ambient pressure: amb_P missing from esp.csv")
    run_ambT = float(np.median(esp["amb_T"][1])) if "amb_T" in esp else float("nan")

    ckp_all = (data[:, names.index(CKP_CHANNEL)].astype(np.uint16) >> CKP_BIT) & 1
    gauge = {ch: volts_to_kpa(data[:, names.index(ch)]) - offsets[ch] for ch in PRESSURE_CHANNELS}
    mean_v = {ch: float(np.mean(data[:, names.index(ch)])) for ch in PRESSURE_CHANNELS}
    dead = [ch for ch in PRESSURE_CHANNELS if mean_v[ch] < DEAD_CHANNEL_V]
    map_all = volts_to_map_kpa(data[:, names.index(MAP_CHANNEL)]) if MAP_CHANNEL in names else None

    windows, mode = build_windows(run_dir, meta, len(data), fs, esp)
    if not windows:
        fail("run too short to analyze")

    rows, trace_pick = [], None
    for w in windows:
        i0 = max(0, int(round((w["t0"] - lj_t0) * fs)))
        i1 = min(len(data), int(round((w["t1"] - lj_t0) * fs)))
        if i1 - i0 < int(0.2 * fs):
            continue
        rpm, _ = window_rpm(ckp_all[i0:i1], fs)
        amb = esp_value(esp, ["amb_P"], w["t0"], w["t1"], pad=5.0)
        if amb != amb:
            amb = run_amb
        row = {"step": w["step"], "t_start_s": round(w["t0"] - lj_t0, 3),
               "duration_s": round((i1 - i0) / fs, 3), "rpm_target": w["rpm_target"],
               "rpm_meas": rpm, "throttle_pct": w["throttle"], "settled": w["settled"],
               "amb_kpa": amb, "amb_C": run_ambT,
               "map_kpa": float(np.median(map_all[i0:i1])) if map_all is not None else np.nan}
        ves, ves_map, peak_sets = [], [], {}
        for ch, cyl in zip(PRESSURE_CHANNELS, CYL_NAMES):
            if ch in dead:
                row.update({f"{cyl}_peak_kpa_abs": np.nan, f"{cyl}_PR": np.nan,
                            f"{cyl}_VE": np.nan, f"{cyl}_cycles": 0})
                continue
            p_abs = gauge[ch][i0:i1] + amb
            peaks = cylinder_peaks(p_abs, fs, rpm)
            peak_sets[cyl] = (i0, p_abs, peaks)
            if len(peaks) == 0:
                pk = pr = ve = np.nan
            else:
                pk = float(np.median(p_abs[peaks]))
                pr = pk / amb
                ve = pr / FULL_CHARGE_PR
                ves.append(ve)
                if row["map_kpa"] == row["map_kpa"]:
                    ves_map.append(pk / row["map_kpa"] / FULL_CHARGE_PR)   # speed-density VE (vs. MAP)
            row.update({f"{cyl}_peak_kpa_abs": pk, f"{cyl}_PR": pr,
                        f"{cyl}_VE": ve, f"{cyl}_cycles": len(peaks)})
        row["VE_mean"] = float(np.mean(ves)) if ves else np.nan            # vs. ambient (includes throttle loss)
        row["VE_map"] = float(np.mean(ves_map)) if ves_map else np.nan     # vs. MAP (what an ECU VE table uses)
        rows.append(row)
        # trace plot: the highest-RPM window that found peaks
        if peak_sets and rpm == rpm and (trace_pick is None or rpm > trace_pick[0]):
            trace_pick = (rpm, w, peak_sets, ckp_all[i0:i1])

    if not rows:
        fail("no usable windows (check steps.csv times vs. the LabJack recording)")
    return rows, mode, dead, offsets, fs, trace_pick, run_amb, tare_dir is not None


# ---------------------------------------------------------------- outputs
def write_csv(rows, path):
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) and v == v else
                            ("" if isinstance(v, float) else v)) for k, v in r.items()})


def heatmap(rows, mode, path, run_name, dead, tared):
    if mode == "sweep":
        rpm_key = lambda r: r["rpm_target"]
        thr_key = lambda r: r["throttle_pct"]
    else:
        rpm_key = lambda r: (round(r["rpm_meas"] / RPM_BIN) * RPM_BIN) if r["rpm_meas"] == r["rpm_meas"] else np.nan
        thr_key = lambda r: (round(r["throttle_pct"] / THR_BIN) * THR_BIN) if r["throttle_pct"] == r["throttle_pct"] else np.nan

    cells = {}
    for r in rows:
        x, y, v = rpm_key(r), thr_key(r), r["VE_mean"]
        if x != x or v != v:
            continue
        cells.setdefault((x, y), []).append((v, r["settled"]))
    if not cells:
        fail("no VE values to plot: no RPM from the CKP (FIO0) or no compression peaks found. Check results.csv")

    xs = sorted({k[0] for k in cells})
    ys_real = sorted({k[1] for k in cells if k[1] == k[1]})
    has_nan_thr = any(k[1] != k[1] for k in cells)
    ys = ys_real + ([np.nan] if has_nan_thr else [])
    grid = np.full((len(ys), len(xs)), np.nan)
    unsettled = np.zeros_like(grid, dtype=bool)
    for (x, y), vals in cells.items():
        yi = len(ys) - 1 if y != y else ys_real.index(y)
        grid[yi, xs.index(x)] = np.mean([v for v, _ in vals])
        unsettled[yi, xs.index(x)] = any(s == 0 for _, s in vals)

    fig, ax = plt.subplots(figsize=(max(6, 0.8 * len(xs) + 3), max(3.5, 0.45 * len(ys) + 2.5)))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(   # tuner style: blue = low VE, red = high VE
        "tuner", ["#1b2fbf", "#1f6fd6", "#1aa0b8", "#1e9a3c", "#6fae1e", "#e3b21b", "#e07a1c", "#d23a1c", "#b5121b"])
    cmap.set_bad("#e6e6e6")
    finite = grid[np.isfinite(grid)]
    vmin, vmax = (float(finite.min()), float(finite.max())) if finite.size else (0, 1)
    if vmax - vmin < 0.05:
        vmin, vmax = vmin - 0.025, vmax + 0.025
    im = ax.imshow(np.ma.masked_invalid(grid), cmap=cmap, vmin=vmin, vmax=vmax,
                   origin="lower", aspect="auto")   # 100% throttle at the top
    for k in range(len(xs) - 1):
        ax.axvline(k + 0.5, color="white", lw=1)
    for k in range(len(ys) - 1):
        ax.axhline(k + 0.5, color="white", lw=1)
    for yi in range(len(ys)):
        for xi in range(len(xs)):
            v = grid[yi, xi]
            if v != v:
                continue
            ax.text(xi, yi, f"{v * 100:.1f}" + ("*" if unsettled[yi, xi] else ""),
                    ha="center", va="center", fontsize=8, color="white", fontweight="bold")
    ax.set_xticks(range(len(xs)), [f"{x:.0f}" for x in xs])
    ax.set_yticks(range(len(ys)), [("n/a" if y != y else f"{y:.0f}") for y in ys])
    ax.set_xlabel("RPM" + (" (target)" if mode == "sweep" else " (measured, binned)"))
    ax.set_ylabel("Throttle %")
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cb.set_label("VE estimate (cells show VE %)")
    cb.ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    cb.outline.set_visible(False)
    notes = [f"VE_est = (P_peak/P_amb) / CR^n, CR={COMPRESSION_RATIO}, n={POLYTROPIC_N}"]
    if not tared:
        notes.append("NO TARE")
    if dead:
        notes.append("ignored (no signal): " + ", ".join(dead))
    if unsettled.any():
        notes.append("* = step did not settle")
    ax.set_title(f"{run_name}: VE estimate (mean of cylinders)\n" + "\n".join(notes),
                 fontsize=9, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def heatmap_map(rows, mode, path, run_name, dead, tared):
    """RPM x MAP table of speed-density VE. Each RPM column is interpolated between the measured
    sweep steps onto MAP_BREAKPOINTS; cells outside the measured MAP range stay blank."""
    if mode == "sweep":
        rpm_key = lambda r: r["rpm_target"]
    else:
        rpm_key = lambda r: (round(r["rpm_meas"] / RPM_BIN) * RPM_BIN) if r["rpm_meas"] == r["rpm_meas"] else np.nan
    cols = {}
    for r in rows:
        x, m, v = rpm_key(r), r["map_kpa"], r["VE_map"]
        if x == x and m == m and v == v:
            cols.setdefault(x, []).append((m, v))
    if not cols:
        fail("no VE values to plot vs. MAP. Check results.csv")
    xs = sorted(cols)
    ys = MAP_BREAKPOINTS
    grid = np.full((len(ys), len(xs)), np.nan)
    for xi, x in enumerate(xs):
        pts = sorted(cols[x])
        m = np.array([p[0] for p in pts]); v = np.array([p[1] for p in pts])
        for yi, y in enumerate(ys):
            if m.size >= 2 and m[0] - 2.0 <= y <= m[-1] + 2.0:      # no extrapolation past measured MAP
                grid[yi, xi] = np.interp(y, m, v)
            elif m.size == 1 and abs(m[0] - y) <= 5.0:
                grid[yi, xi] = v[0]

    fig, ax = plt.subplots(figsize=(max(6, 0.8 * len(xs) + 3), max(3.5, 0.45 * len(ys) + 2.5)))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(   # tuner style: blue = low VE, red = high VE
        "tuner", ["#1b2fbf", "#1f6fd6", "#1aa0b8", "#1e9a3c", "#6fae1e", "#e3b21b", "#e07a1c", "#d23a1c", "#b5121b"])
    cmap.set_bad("#e6e6e6")
    finite = grid[np.isfinite(grid)]
    vmin, vmax = (float(finite.min()), float(finite.max())) if finite.size else (0, 1)
    if vmax - vmin < 0.05:
        vmin, vmax = vmin - 0.025, vmax + 0.025
    im = ax.imshow(np.ma.masked_invalid(grid), cmap=cmap, vmin=vmin, vmax=vmax, origin="lower", aspect="auto")
    for k in range(len(xs) - 1):
        ax.axvline(k + 0.5, color="white", lw=1)
    for k in range(len(ys) - 1):
        ax.axhline(k + 0.5, color="white", lw=1)
    for yi in range(len(ys)):
        for xi in range(len(xs)):
            v = grid[yi, xi]
            if v == v:
                ax.text(xi, yi, f"{v * 100:.1f}", ha="center", va="center", fontsize=8,
                        color="white", fontweight="bold")
    ax.set_xticks(range(len(xs)), [f"{x:.0f}" for x in xs])
    ax.set_yticks(range(len(ys)), [f"{y:.0f}" for y in ys])
    ax.set_xlabel("RPM" + (" (target)" if mode == "sweep" else " (measured, binned)"))
    ax.set_ylabel("Manifold pressure (kPa abs)")
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cb.set_label("VE (cells show VE %)")
    cb.ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    cb.outline.set_visible(False)
    notes = [f"VE = (P_peak/MAP) / CR^n, CR={COMPRESSION_RATIO}, n={POLYTROPIC_N}; "
             "interpolated between sweep steps, gray = outside measured MAP"]
    if not tared:
        notes.append("NO TARE")
    if dead:
        notes.append("ignored (no signal): " + ", ".join(dead))
    ax.set_title(f"{run_name}: VE table (mean of cylinders)\n" + "\n".join(notes), fontsize=9, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def trace_plot(trace_pick, fs, path):
    """Small multiples: one panel per cylinder (+ CKP), 0.25 s of the highest-RPM window."""
    if trace_pick is None:
        return
    rpm, w, peak_sets, ckp = trace_pick
    n_show = min(len(ckp), int(0.25 * fs))
    t_ms = np.arange(n_show) / fs * 1000.0
    panels = list(peak_sets.items())
    fig, axes = plt.subplots(len(panels) + 1, 1, figsize=(9, 1.6 * (len(panels) + 1)), sharex=True)
    for ax, (cyl, (_, p_abs, peaks)) in zip(axes, panels):
        ax.plot(t_ms, p_abs[:n_show], color="#2a5d9f", lw=1.2)
        pk = peaks[peaks < n_show]
        ax.plot(t_ms[pk], p_abs[pk], "o", ms=6, mfc="#1a1a1a", mec="white", mew=1.5)
        ax.set_ylabel(f"{cyl}\nkPa abs", fontsize=8)
        ax.grid(alpha=0.25, lw=0.5)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    axes[-1].step(t_ms, ckp[:n_show], where="post", color="#555555", lw=1.0)
    axes[-1].set_ylabel("CKP\n(FIO0)", fontsize=8)
    axes[-1].set_yticks([0, 1])
    axes[-1].set_xlabel("ms into window")
    for s in ("top", "right"):
        axes[-1].spines[s].set_visible(False)
    fig.suptitle(f"Step {w['step']}: {rpm:.0f} RPM - dots = detected compression peaks", fontsize=10, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="run folder (contains lj.bin)") # required
    ap.add_argument("--tare", help="tare folder: engine stopped, sensors at atmosphere") # optional
    a = ap.parse_args()

    rows, mode, dead, offsets, fs, trace_pick, run_amb, tared = analyze(a.run, a.tare)
    run_name = os.path.basename(os.path.normpath(a.run))
    write_csv(rows, os.path.join(a.run, "results.csv"))
    trace_plot(trace_pick, fs, os.path.join(a.run, "traces.png"))
    if any(r["map_kpa"] == r["map_kpa"] for r in rows):     # MAP logged -> RPM x MAP table
        heatmap_map(rows, mode, os.path.join(a.run, "ve_heatmap.png"), run_name, dead, tared)
    else:                                                  # older runs without AIN4 -> RPM x throttle
        heatmap(rows, mode, os.path.join(a.run, "ve_heatmap.png"), run_name, dead, tared)

    print(f"{run_name}: {mode} run, {len(rows)} windows, fs={fs:.0f} Hz, ambient {run_amb:.2f} kPa")
    if tared:
        print("tare offsets (kPa): " + ", ".join(f"{ch} {v:+.2f}" for ch, v in offsets.items()))
    if dead:
        print("WARNING: no signal on " + ", ".join(dead) + " (mean below "
              f"{DEAD_CHANNEL_V} V) - those cylinders were skipped")
    print(f"{'step':>4} {'rpm_tgt':>8} {'rpm':>7} {'thr%':>5} {'MAP':>6} {'VE_mean':>8}  cycles/cyl")
    for r in rows:
        cyc = "/".join(str(r[f"{c}_cycles"]) for c in CYL_NAMES)
        fmt = lambda v, f: ("--" if v != v else format(v, f))
        print(f"{r['step']:>4} {fmt(r['rpm_target'], '.0f'):>8} {fmt(r['rpm_meas'], '.0f'):>7} "
              f"{fmt(r['throttle_pct'], '.0f'):>5} {fmt(r['map_kpa'], '.1f'):>6} {fmt(r['VE_mean'] * 100, '.1f'):>7}%  {cyc}"
              + ("" if r["settled"] else "  (not settled)"))


if __name__ == "__main__":
    main()
