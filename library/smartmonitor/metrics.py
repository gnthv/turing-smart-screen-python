# SPDX-License-Identifier: GPL-3.0-or-later
#
# Values for the SmartMonitor screen: built-in PC metrics (psutil, NVIDIA via GPUtil) and own metrics from a command.
# The screen shows whole numbers only (-32768..32767); with "thousands: true" on a number widget it shows value/1000
# with one decimal (e.g. ram.used.mb 9830 -> "9.8").

import os
import re
import subprocess
import sys
import threading
import time

import psutil

BUILTIN = {
    "cpu.percent": "CPU-belasting in %",
    "cpu.freq": "CPU-kloksnelheid in MHz (met thousands: GHz)",
    "ram.percent": "werkgeheugen in gebruik in %",
    "ram.used.mb": "werkgeheugen in gebruik in MB (met thousands: GB)",
    "ram.total.mb": "werkgeheugen totaal in MB (met thousands: GB)",
    "disk.percent": "systeemschijf vol in %",
    "disk.free.gb": "systeemschijf vrij in GB",
    "net.down.kbs": "download in KB/s",
    "net.up.kbs": "upload in KB/s",
    "net.down.mbit": "download in Mbit/s",
    "net.up.mbit": "upload in Mbit/s",
    "gpu.percent": "GPU-belasting in % (NVIDIA)",
    "gpu.temp": "GPU-temperatuur in °C (NVIDIA)",
    "gpu.mem.percent": "videogeheugen in gebruik in % (NVIDIA)",
    "gpu.mem.used.mb": "videogeheugen in gebruik in MB (NVIDIA)",
    "uptime.hours": "uren sinds het opstarten",
    "processes": "aantal processen",
}

NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def first_number(text):
    match = NUMBER.search(text or "")
    return float(match.group()) if match else None


class CommandMetric:
    """Runs a command in the background every `every` seconds; the value is the first number of its output."""

    def __init__(self, name, command, every=10, scale=1, runner=None):
        self.name = name
        self.command = command
        self.every = float(every)
        self.scale = float(scale)
        self.value = None
        self.error = None
        self._runner = runner or self._run_shell
        self._started = 0.0
        self._busy = False
        self._lock = threading.Lock()

    @staticmethod
    def _run_shell(command, timeout):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        done = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout,
                              creationflags=flags)
        if done.returncode != 0:
            raise RuntimeError(f"exit code {done.returncode}: {done.stderr.strip()[:200]}")
        return done.stdout

    def _work(self):
        try:
            number = first_number(self._runner(self.command, min(self.every, 30.0)))
            with self._lock:
                if number is None:
                    self.error = "geen getal in de uitvoer"
                else:
                    self.value, self.error = number * self.scale, None
        except Exception as error:  # noqa: BLE001 - a failing command only leaves its value empty
            with self._lock:
                self.error = str(error)
        finally:
            self._busy = False

    def poll(self, now=None, wait=False):
        now = time.monotonic() if now is None else now
        if not self._busy and (self._started == 0.0 or now - self._started >= self.every):
            self._busy, self._started = True, now
            if wait:
                self._work()
            else:
                threading.Thread(target=self._work, daemon=True).start()
        with self._lock:
            return self.value


class Metrics:
    def __init__(self, custom=None, gpu_reader=None):
        self.custom = {name: CommandMetric(name, d["command"], d.get("every", 10), d.get("scale", 1))
                       for name, d in (custom or {}).items()}
        self._gpu_reader = gpu_reader or self._read_nvidia
        self._gpu_cache, self._gpu_time = None, 0.0
        self._net_last = None
        psutil.cpu_percent(interval=None)  # the first call only starts the measurement

    def known(self, name):
        return name in BUILTIN or name in self.custom

    @staticmethod
    def _read_nvidia():
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
        except Exception:  # noqa: BLE001 - no NVIDIA card or no nvidia-smi
            return None
        if not gpus:
            return None
        gpu = gpus[0]
        return {"gpu.percent": gpu.load * 100, "gpu.temp": gpu.temperature,
                "gpu.mem.percent": (gpu.memoryUsed / gpu.memoryTotal * 100) if gpu.memoryTotal else None,
                "gpu.mem.used.mb": gpu.memoryUsed}

    def _gpu(self):
        now = time.monotonic()
        if now - self._gpu_time >= 2.0:  # nvidia-smi is slow: at most every 2 seconds
            self._gpu_cache, self._gpu_time = self._gpu_reader(), now
        return self._gpu_cache or {}

    def _network(self):
        counters = psutil.net_io_counters()
        now = time.monotonic()
        rates = {}
        if self._net_last:
            seconds = max(now - self._net_last[0], 0.001)
            down = max(counters.bytes_recv - self._net_last[1], 0) / seconds
            up = max(counters.bytes_sent - self._net_last[2], 0) / seconds
            rates = {"net.down.kbs": down / 1024, "net.up.kbs": up / 1024,
                     "net.down.mbit": down * 8 / 1e6, "net.up.mbit": up * 8 / 1e6}
        self._net_last = (now, counters.bytes_recv, counters.bytes_sent)
        return rates

    def read(self, names, wait=False):
        """{name: value} for the names that can be read now; a metric that can't be read is left out."""
        names = set(names)
        values = {}
        if "cpu.percent" in names:
            values["cpu.percent"] = psutil.cpu_percent(interval=None)
        if "cpu.freq" in names:
            frequency = psutil.cpu_freq()
            if frequency:
                values["cpu.freq"] = frequency.current
        if names & {"ram.percent", "ram.used.mb", "ram.total.mb"}:
            memory = psutil.virtual_memory()
            values.update({"ram.percent": memory.percent, "ram.used.mb": (memory.total - memory.available) / 2 ** 20,
                           "ram.total.mb": memory.total / 2 ** 20})
        if names & {"disk.percent", "disk.free.gb"}:
            root = (os.environ.get("SystemDrive", "C:") + "\\") if sys.platform == "win32" else "/"
            disk = psutil.disk_usage(root)
            values.update({"disk.percent": disk.percent, "disk.free.gb": disk.free / 2 ** 30})
        if any(name.startswith("net.") for name in names):
            values.update(self._network())
        if any(name.startswith("gpu.") for name in names):
            values.update({k: v for k, v in self._gpu().items() if v is not None})
        if "uptime.hours" in names:
            values["uptime.hours"] = (time.time() - psutil.boot_time()) / 3600
        if "processes" in names:
            values["processes"] = len(psutil.pids())
        for name, metric in self.custom.items():
            if name in names:
                value = metric.poll(wait=wait)
                if value is not None:
                    values[name] = value
        return {name: value for name, value in values.items() if name in names}
