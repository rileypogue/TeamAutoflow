# LJM DAQ: LabJack T7 stream logger that runs in a background thread
#
# Output (in outdir):
#   lj.bin     float32 volts, interleaved by scan: np.fromfile(...).reshape(-1, n_channels)
#   meta.json  channels, requested/actual scan rate, start times, scan count, errors

import json
import os
import threading
import time

import numpy as np
from labjack import ljm

FS = 15000  # Hz per channel, subject to change
# AIN0-3: pressure sensors (volts). FIO_STATE: FIO0-7 as one 8-bit value; bit 0 = FIO0 = crank tooth signal.
LJ_NAMES = ["AIN0", "AIN1", "AIN2", "AIN3", "FIO_STATE"]

class LJLogger:
    def __init__(self, names=LJ_NAMES, fs=FS):
        self.names = list(names)
        self.fs = fs
        self._stop = threading.Event()
        self._thread = None
        self.last_scan = None
        self.info = {}

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, outdir, minutes=None, meta=None):
        """Start streaming in a background thread. minutes=None -> run until stop()."""
        if self.running:
            raise RuntimeError("LabJack logger is already running")
        os.makedirs(outdir, exist_ok=True)
        self._stop.clear()
        self.info = {
            "channels": self.names,
            "requested_fs": self.fs,
            "dtype": "float32",
            "layout": "interleaved by scan: [scan][channel], volts",
            "scans": 0,
            "errors": [],
            "retries": [],
        }
        if meta:
            self.info.update(meta)
        self._thread = threading.Thread(
            target=self._run, args=(outdir, minutes), daemon=True, name="labjack_logger"
        )  # daemon thread will not stop the program from exiting
        self._thread.start()

    def stop(self, timeout=5.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def lj_cleanup(self, h):
        for fn in (lambda: ljm.eStreamStop(h), lambda: ljm.eWriteName(h, "LUA_RUN", 0)):
            try:
                fn()
            except Exception:
                pass

    def _run(self, outdir, minutes):
        h = None
        done = 0
        try:
            h = ljm.openS("T7", "ANY", "ANY")
            try:
                ljm.eStreamStop(h)      # clear a stream left running by a crashed/killed run
            except Exception:
                pass
            n = len(self.names)
            ljm.eWriteNames(
                h, 5,
                ["AIN_ALL_RANGE", "STREAM_SETTLING_US", "STREAM_RESOLUTION_INDEX",
                 "STREAM_TRIGGER_INDEX", "STREAM_CLOCK_SOURCE"],
                [10.0, 0, 0, 0, 0],
            )
            addrs = ljm.namesToAddresses(n, self.names)[0]

            try:
                ljm.eWriteName(h, "FIO_DIRECTION", 0)   # make sure FIO lines (the crank signal on FIO0) are inputs
            except Exception as e:
                self.info["retries"].append(f"could not set FIO direction: {e}")

            try:
                ljm.eStreamStop(h)  # make sure there's no old stream
            except Exception:
                pass

            scans_per_read = max(1, self.fs // 10)  # read ~10 times per second
            total = None if minutes is None else int(self.fs * 60 * minutes)

            with open(os.path.join(outdir, "lj.bin"), "wb") as f:
                started = False
                for attempt in range(3):
                    try:
                        self.info["lj_t0_before"] = time.time()
                        actual = ljm.eStreamStart(h, scans_per_read, n, addrs, self.fs)
                        self.info["lj_t0"] = time.time()  # stream is running by now
                        self.info["actual_fs"] = actual
                        started = True
                        break
                    except Exception as e:
                        self.info["retries"].append(f"stream start failed (try {attempt + 1}): {e}")
                        try:
                            ljm.eStreamStop(h)
                        except Exception:
                            pass
                        time.sleep(0.5)
                if not started:
                    raise RuntimeError("LabJack stream failed to start after 3 tries")

                while not self._stop.is_set() and (total is None or done < total):
                    data, dev_backlog, ljm_backlog = ljm.eStreamRead(h)
                    np.asarray(data, dtype=np.float32).tofile(f)
                    done += scans_per_read
                    arr = np.asarray(data, dtype=np.float32)
                    arr.tofile(f)
                    self.last_scan = arr.reshape(-1, len(self.names)).mean(axis=0)   # average of the last ~0.1 s
                f.flush()
        except Exception as e:
            self.info["errors"].append(f"LJ failed: {e}")
        finally:
            self.info["scans"] = done
            if h is not None:
                self.lj_cleanup(h)
                try:
                    ljm.close(h)
                except Exception:
                    pass
            try:
                with open(os.path.join(outdir, "meta.json"), "w") as mf:
                    json.dump(self.info, mf, indent=2, default=str)
            except Exception:
                pass
