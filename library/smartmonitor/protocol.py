# SPDX-License-Identifier: GPL-3.0-or-later
#
# Wire protocol of the SmartMonitor HID screen: value updates, the clock and the theme upload (YMODEM over HID).
# Every report is 64 bytes; the transport adds the HID report ID byte 0x00 in front of it.

import datetime
import time

REPORT_SIZE = 64
MAX_PAIRS = 20  # value pairs per report
CLOCK_TAG = 21  # tag of the date/time widgets; never used for a value

ACK = 0x06
NAK = 0x15
SOH = 0x01  # block of 128 data bytes
STX = 0x02  # block of 1024 data bytes
EOT = 0x04
PAD = 0x1A
THEME_FILE_NAME = b"img.dat"

# Timings of the upload (see docs/protocol.md of sensordeck, "The numbers of the upload")
REOPEN_FIRST_PAUSE = 1.0
REOPEN_RETRY_PAUSE = 0.5
REOPEN_ATTEMPTS = 20
ANSWER_TIMEOUT = 10.0
MAX_WRITES = 10


class ReadTimeout(Exception):
    """No input report arrived within the time limit."""


class UploadError(Exception):
    def __init__(self, stage, message, block=0):
        self.stage = stage
        self.block = block
        where = f"data block {block}" if stage == "data" else stage
        super().__init__(f"theme upload failed at {where}: {message}")


def _report(data: bytes) -> bytes:
    if len(data) > REPORT_SIZE:
        raise ValueError(f"report of {len(data)} bytes, at most {REPORT_SIZE}")
    return bytes(data).ljust(REPORT_SIZE, b"\x00")


def clamp16(value) -> int:
    """A whole number the screen can show: rounded, then limited to -32768..32767."""
    number = int(round(float(value)))
    return max(-32768, min(32767, number))


def value_reports(values):
    """Value update reports for a list of (tag, value) pairs, at most 20 pairs per report.

    Report: 0x02, number of pairs, then per pair the tag and the value as signed 16 bits, high byte first."""
    pairs = []
    for tag, value in values:
        tag = int(tag)
        if not 1 <= tag <= 255 or tag == CLOCK_TAG:
            raise ValueError(f"tag {tag}: want 1..255 and not {CLOCK_TAG}")
        pairs.append((tag, clamp16(value) & 0xFFFF))
    reports = []
    for start in range(0, len(pairs), MAX_PAIRS):
        chunk = pairs[start:start + MAX_PAIRS]
        data = bytearray([0x02, len(chunk)])
        for tag, bits in chunk:
            data += bytes([tag, bits >> 8, bits & 0xFF])
        reports.append(_report(data))
    return reports


def clock_report(moment: datetime.datetime) -> bytes:
    """The clock report. The screen shows the last clock it got and does not advance it: send it every second."""
    if not 2000 <= moment.year <= 2255:
        raise ValueError("year must be 2000..2255")
    return _report(bytes([0x03, 0x01, 0x15, moment.year - 2000, moment.month, moment.day,
                          moment.hour, moment.minute, moment.second, moment.isoweekday(), 0x64]))


def reset_report() -> bytes:
    return _report(b"\x01reset")


def ymodem_report() -> bytes:
    return _report(b"ymodem")


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def frame_block(start: int, number: int, data: bytes) -> bytes:
    crc = crc16_xmodem(data)
    return bytes([start, number & 0xFF, 0xFF - (number & 0xFF)]) + bytes(data) + bytes([crc >> 8, crc & 0xFF])


def header_block(size: int) -> bytes:
    data = bytearray(128)
    name_and_size = THEME_FILE_NAME + b"\x00" + str(int(size)).encode("ascii")
    data[:len(name_and_size)] = name_and_size
    return frame_block(SOH, 0, bytes(data))


def data_blocks(theme: bytes):
    blocks = []
    for index, start in enumerate(range(0, len(theme), 1024), start=1):
        chunk = theme[start:start + 1024].ljust(1024, bytes([PAD]))
        blocks.append(frame_block(STX, index, chunk))
    return blocks


def closing_block() -> bytes:
    return frame_block(SOH, 0, bytes(128))


def to_reports(block: bytes):
    """A YMODEM block is carried as consecutive 64-byte reports, the last one filled with 0x00."""
    return [_report(block[start:start + REPORT_SIZE]) for start in range(0, len(block), REPORT_SIZE)]


def upload_theme(transport, theme: bytes, progress=None, sleep=time.sleep):
    """Upload a compiled theme (img.dat). The screen reboots twice; takes about 10 seconds for a full theme.

    transport: write(report64), read(timeout_s) -> report64 (raises ReadTimeout), reopen()
    progress(stage, block, blocks) is called before each stage and each data block."""
    blocks = data_blocks(theme)
    total = len(blocks)

    def tell(stage, block=0):
        if progress:
            progress(stage, block, total)

    def write_all(reports, stage, block=0):
        try:
            for report in reports:
                transport.write(report)
        except Exception as error:  # noqa: BLE001 - every transport error ends the upload
            raise UploadError(stage, str(error), block) from error

    def send_item(reports, stage, block=0):
        for _ in range(MAX_WRITES):
            write_all(reports, stage, block)
            try:
                answer = transport.read(ANSWER_TIMEOUT)
            except ReadTimeout as error:
                raise UploadError(stage, "no answer from the screen", block) from error
            except Exception as error:  # noqa: BLE001
                raise UploadError(stage, str(error), block) from error
            if answer[0] == ACK:
                return
            if answer[0] != NAK:
                raise UploadError(stage, f"unexpected answer 0x{answer[0]:02x}", block)
        raise UploadError(stage, f"the screen refused it {MAX_WRITES} times", block)

    def reopen(stage):
        sleep(REOPEN_FIRST_PAUSE)
        last = None
        for attempt in range(REOPEN_ATTEMPTS):
            try:
                transport.reopen()
                return
            except Exception as error:  # noqa: BLE001 - an I/O error while the screen enumerates is normal
                last = error
                if attempt < REOPEN_ATTEMPTS - 1:
                    sleep(REOPEN_RETRY_PAUSE)
        raise UploadError(stage, f"the screen did not come back after {REOPEN_ATTEMPTS} attempts ({last})")

    tell("reset")
    write_all([reset_report()], "reset")
    tell("reopen after reset")
    reopen("reopen after reset")
    tell("ymodem command")
    send_item([ymodem_report()], "ymodem command")
    tell("header block")
    send_item(to_reports(header_block(len(theme))), "header block")
    for index, block in enumerate(blocks, start=1):
        tell("data", index)
        send_item(to_reports(block), "data", index)
    tell("end of transmission")
    send_item([_report(bytes([EOT]))], "end of transmission")
    tell("closing block")
    write_all(to_reports(closing_block()), "closing block")
    tell("reopen after upload")
    reopen("reopen after upload")
