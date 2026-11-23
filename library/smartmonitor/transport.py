# SPDX-License-Identifier: GPL-3.0-or-later
#
# HID transport for the SmartMonitor screen through hidapi (Windows, Linux, macOS).

from library.smartmonitor.protocol import REPORT_SIZE, ReadTimeout

VENDOR_ID = 0x0483
PRODUCT_ID = 0x0065
USAGE_PAGE = 0xFF00  # vendor-defined collection of the screen


class ScreenNotFound(Exception):
    pass


def find_screens():
    """The SmartMonitor screens that are plugged in, as hidapi device dictionaries."""
    import hid  # imported here so that the rest of the package works without hidapi (tests, previews)
    found = []
    for info in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        # Windows and macOS give the usage page; Linux hidraw gives 0. Never take another collection of the device.
        if info.get("usage_page") in (USAGE_PAGE, 0):
            found.append(info)
    return found


class HidTransport:
    """Writes and reads 64-byte reports. The screen uses no numbered reports, so report ID 0x00 goes in front."""

    def __init__(self, path=None):
        self.path = path  # a fixed device path, or None for the first screen found
        self.device = None

    def open(self):
        import hid
        screens = find_screens()
        if self.path is not None:
            screens = [s for s in screens if s["path"] == self.path]
        if not screens:
            raise ScreenNotFound("no SmartMonitor screen (USB 0483:0065) found")
        device = hid.device()
        device.open_path(screens[0]["path"])
        self.device = device

    def close(self):
        if self.device is not None:
            try:
                self.device.close()
            finally:
                self.device = None

    def reopen(self):
        self.close()
        self.open()

    def write(self, report: bytes):
        if self.device is None:
            raise IOError("screen not open")
        if len(report) != REPORT_SIZE:
            raise ValueError(f"report of {len(report)} bytes, want {REPORT_SIZE}")
        written = self.device.write(b"\x00" + report)
        if written < 0:
            raise IOError(f"write to the screen failed: {self.device.error()}")

    def read(self, timeout_s: float) -> bytes:
        if self.device is None:
            raise IOError("screen not open")
        data = self.device.read(REPORT_SIZE, int(timeout_s * 1000))
        if not data:
            raise ReadTimeout("no answer from the screen")
        return bytes(data[:REPORT_SIZE]).ljust(REPORT_SIZE, b"\x00")
