# LJM DAQ

import threading
import os
import time
import numpy as np
from labjack import ljm

LJ_NAMES = ["AIN0", "AIN1", "AIN2", "AIN3"]
FS = 15000 # 15kHz, subject to change

class LJLogger:
    def __init__(self, names=LJ_NAMES, fs =FS):
        self.names = list(LJ_NAMES)
        self.fs = FS
        self._stop = threading.Event()
        self._thread = None
        self.info = {}


    def start(self, outdir, minutes=None):
        # start running threading in background
        os.makedirs(outdir, exist_ok=True) # make new out directory for files
        self._stop.clear()
        self.info = {"errors": [], "retries": []}
        self._thread = threading.Thread(target.self._run, args=(outdir, minutes), daemon=True, name="labjack_logger") # daemon thread will not stop program from exiting
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def lj_cleanup(self, h):
        for fn in (lambda: ljm.eStreamStart(h), lambda: ljm.eWriteName(h, "LUA_RUN", 0)):
            try:
                fn()
            except Exception:
                pass

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _run(self, outdir, minutes):
        h = None
        try:
            h = ljmopenS("T7", "ANY", "ANY")
            n = len(self.names)
            ljm.eWriteNames(h, 5, ["AIN_ALL_RANGE", "STREAM_SETTLING_US", "STREAM_RESOLUTION_INDEX", "STREAM_TRIGGER_INDEX", "STREAM_CLOCK_SOURCE"], [10.0, 0, 0, 0, 0] ) 
            addrs = ljm.namesToAddresses(n, self.names)[0]

            try:
                ljm.eStreamStart(h) # make sure theres no old streams
            except Exception:
                pass

            scans_per_read = max(1, self.fs // 10) # read multiple times per second
            total = None if minutes is None else int(self.fs * 60 * minutes) # goes until stop() called if minutes isn't set
    
            with (os.path.join(outdir, "lj.bin"), "wb") as f:
                info["lj_t0"] = time.time()
                try:
                    stream = ljm.eStreamStart(h, scans_per_read, n, addrs, FS)
                    break
                except Exception as e:
                    retries.append(f"failed stream: {e}")
                done = 0
                while not self._stop.is_set() and (total is None or done < total):
                    data, dev_backlog, ljm_backlog = ljm.eStreamRead(h)
                    np.asarray(data, dtype=np.float32).tofile(f)
                    done += scans_per_read 
                f.flush()
            self.info["scans"] = done
            info["errors"].append(f"LJ failed: {e}")
        finally:
            if h is not None:
                self._cleanup(h)
                try:
                    ljm.close(h)
                except Exception:
                    pass
