# SPDX-License-Identifier: GPL-3.0-or-later
#
# Tests for the SmartMonitor 3.5" HID screen (library/smartmonitor, smartmonitor.py), with an emulated screen.
# Run from the project folder: python -m unittest tests.library.smartmonitor.test_smartmonitor -v

import datetime
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from library.smartmonitor import protocol  # noqa: E402
from library.smartmonitor.metrics import CommandMetric, Metrics, first_number  # noqa: E402
from library.smartmonitor import theme as th  # noqa: E402

DEFAULT_THEME = os.path.join(ROOT, "res", "smartmonitor", "themes", "standaard")
FONT = "res/fonts/roboto/Roboto-Bold.ttf"


# ---------------------------------------------------------------- an emulated screen

class FakeScreen:
    """Speaks the screen's side of the protocol: reboots on 'reset', receives a theme over YMODEM, ACKs/NAKs."""

    def __init__(self, nak_blocks=(), always_nak=None, silent=False, fail_reopens=0):
        self.open = True
        self.rebooting = False
        self.reopens = 0
        self.fail_reopens = fail_reopens
        self.answers = []
        self.mode = "idle"
        self.stream = b""
        self.size = None
        self.received = b""
        self.blocks_seen = []
        self.nak_once = set(nak_blocks)
        self.always_nak = always_nak
        self.silent = silent
        self.values = {}
        self.clock = None
        self.finished = False

    # transport API
    def write(self, report):
        assert len(report) == 64
        if self.rebooting:
            raise IOError("device is rebooting")
        if self.mode == "idle":
            if report[0] == 0x02:
                for i in range(report[1]):
                    tag, high, low = report[2 + 3 * i:5 + 3 * i]
                    self.values[tag] = struct.unpack(">h", bytes([high, low]))[0]
            elif report[0] == 0x03:
                self.clock = report[:11]
            elif report[:6] == b"\x01reset":
                self.rebooting = True  # a new upload starts from scratch (the screen accepts one theme after another)
                self.size, self.received, self.blocks_seen, self.finished = None, b"", [], False
            elif report[:6] == b"ymodem":
                self.mode = "ymodem"
                self._answer(protocol.ACK)
            return
        # YMODEM: blocks arrive as consecutive reports
        self.stream += report
        first = self.stream[0]
        need = {protocol.SOH: 133, protocol.STX: 1029, protocol.EOT: 1}.get(first)
        assert need is not None, f"unexpected start byte {first}"
        if len(self.stream) < need:
            return
        block, self.stream = self.stream[:need], b""
        if first == protocol.EOT:
            self._answer(protocol.ACK)
            return
        number, complement = block[1], block[2]
        data, crc = block[3:-2], block[-2] << 8 | block[-1]
        assert number + complement == 255
        assert crc == protocol.crc16_xmodem(data)
        if first == protocol.SOH and number == 0 and self.size is not None:
            assert not any(data), "closing block must be empty"
            self.finished, self.mode, self.rebooting = True, "idle", True
            return
        if first == protocol.SOH:
            name, size = data.split(b"\x00")[:2]
            assert name == b"img.dat"
            self.size = int(size)
            self._answer(protocol.ACK)
            return
        self.blocks_seen.append(number)
        if self.always_nak == number or number in self.nak_once:
            self.nak_once.discard(number)
            self._answer(protocol.NAK)
            return
        expected = (len(self.received) // 1024 + 1) & 0xFF
        assert number == expected, f"block {number}, want {expected}"
        self.received += data
        self._answer(protocol.ACK)

    def read(self, timeout):
        if self.silent or not self.answers:
            raise protocol.ReadTimeout("no answer")
        return self.answers.pop(0)

    def reopen(self):
        if self.fail_reopens:
            self.fail_reopens -= 1
            raise IOError("not enumerated yet")
        self.reopens += 1
        self.rebooting = False

    def _answer(self, byte):
        self.answers.append(bytes([byte]).ljust(64, b"\x00"))

    def theme(self):
        return self.received[:self.size]


def no_sleep(_seconds):
    pass


def write_theme(folder, text):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "theme.yaml"), "w", encoding="utf-8") as stream:
        stream.write(text)
    return folder


# ---------------------------------------------------------------- protocol

class ProtocolTest(unittest.TestCase):
    def test_value_report_layout(self):
        report = protocol.value_reports([(1, 0x1234), (255, 1)])[0]
        self.assertEqual(report, bytes.fromhex("0202011234ff0001").ljust(64, b"\x00"))
        self.assertEqual(protocol.value_reports([(1, -1)])[0][:5], bytes.fromhex("020101ffff"))

    def test_value_reports_split_after_20_pairs(self):
        reports = protocol.value_reports([(tag, tag) for tag in range(1, 23) if tag != 21])
        self.assertEqual([r[1] for r in reports], [20, 1])

    def test_values_are_rounded_and_limited(self):
        self.assertEqual(protocol.clamp16(12.6), 13)
        self.assertEqual(protocol.clamp16(40000), 32767)
        self.assertEqual(protocol.clamp16(-50000), -32768)

    def test_refuses_bad_tags(self):
        for tag in (0, 21, 256):
            with self.assertRaises(ValueError):
                protocol.value_reports([(tag, 1)])

    def test_clock_report(self):
        report = protocol.clock_report(datetime.datetime(2026, 11, 14, 15, 4, 5))  # a Saturday
        self.assertEqual(report[:11], bytes.fromhex("0301151a0b0e0f04050664"))
        with self.assertRaises(ValueError):
            protocol.clock_report(datetime.datetime(1999, 1, 1))

    def test_crc_and_blocks(self):
        self.assertEqual(protocol.crc16_xmodem(b"123456789"), 0x31C3)
        header = protocol.header_block(5000)
        self.assertEqual(len(header), 133)
        self.assertEqual(header[:3], bytes([protocol.SOH, 0, 255]))
        self.assertTrue(header[3:].startswith(b"img.dat\x005000\x00"))
        blocks = protocol.data_blocks(b"x" * 1025)
        self.assertEqual([len(b) for b in blocks], [1029, 1029])
        self.assertEqual(blocks[1][1:3], bytes([2, 253]))
        self.assertEqual(blocks[1][4:1027], bytes([protocol.PAD]) * 1023)
        reports = protocol.to_reports(blocks[0])
        self.assertEqual(len(reports), 17)
        self.assertEqual(reports[-1][5:], bytes(59))


class UploadTest(unittest.TestCase):
    def test_upload_reaches_the_screen_intact(self):
        theme = bytes(range(256)) * 1100  # 281 600 bytes, not a multiple of 1024
        screen = FakeScreen()
        stages = []
        protocol.upload_theme(screen, theme, lambda s, b, t: stages.append(s), sleep=no_sleep)
        self.assertEqual(screen.theme(), theme)
        self.assertTrue(screen.finished)
        self.assertEqual(screen.reopens, 2)  # after the reset and after the upload
        self.assertEqual(stages[0], "reset")
        self.assertEqual(stages[-1], "reopen after upload")

    def test_block_numbers_wrap_after_255(self):
        theme = b"\xab" * (300 * 1024)
        screen = FakeScreen()
        protocol.upload_theme(screen, theme, sleep=no_sleep)
        self.assertEqual(screen.theme(), theme)
        self.assertIn(0, screen.blocks_seen)  # block 256 is numbered 0

    def test_nak_sends_the_block_again(self):
        screen = FakeScreen(nak_blocks={3})
        theme = b"\x11" * 5000
        protocol.upload_theme(screen, theme, sleep=no_sleep)
        self.assertEqual(screen.theme(), theme)
        self.assertEqual(screen.blocks_seen.count(3), 2)

    def test_refused_block_stops_after_ten_writes(self):
        screen = FakeScreen(always_nak=2)
        with self.assertRaises(protocol.UploadError) as caught:
            protocol.upload_theme(screen, b"\x22" * 5000, sleep=no_sleep)
        self.assertEqual((caught.exception.stage, caught.exception.block), ("data", 2))
        self.assertEqual(screen.blocks_seen.count(2), protocol.MAX_WRITES)

    def test_no_answer_is_an_error(self):
        screen = FakeScreen(silent=True)
        with self.assertRaises(protocol.UploadError) as caught:
            protocol.upload_theme(screen, b"\x33" * 100, sleep=no_sleep)
        self.assertEqual(caught.exception.stage, "ymodem command")

    def test_screen_that_does_not_come_back(self):
        screen = FakeScreen(fail_reopens=protocol.REOPEN_ATTEMPTS)
        with self.assertRaises(protocol.UploadError) as caught:
            protocol.upload_theme(screen, b"\x44" * 100, sleep=no_sleep)
        self.assertEqual(caught.exception.stage, "reopen after reset")

    def test_slow_enumeration_is_normal(self):
        screen = FakeScreen(fail_reopens=5)
        protocol.upload_theme(screen, b"\x55" * 3000, sleep=no_sleep)
        self.assertEqual(screen.theme(), b"\x55" * 3000)


# ---------------------------------------------------------------- theme

class ThemeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def theme(self, body, name="t"):
        return write_theme(os.path.join(self.tmp, name), body)

    def test_default_theme_compiles_within_the_limit(self):
        compiled = th.compile_theme(DEFAULT_THEME)
        data = compiled.data
        self.assertLessEqual(len(data), th.MAX_THEME_BYTES)
        self.assertEqual(data[0], 0x96)
        records = th.read_records(data)
        background = records[0]
        self.assertEqual((background["slot"], background["type"], background["offset"]),
                         (1, th.TYPE_BACKGROUND, th.TABLE_SIZE))
        offsets = []
        for record in records:
            self.assertLessEqual(record["x"] + record["width"], 480)
            self.assertLessEqual(record["y"] + record["height"], 320)
            self.assertEqual(record["index"], record["slot"] - 1)
            if record["type"] in (th.TYPE_NUMBER, th.TYPE_BAR):
                self.assertIn(record["tag"], compiled.tags.values())
            if "offset" in record:
                offsets.append(record["offset"])
        self.assertEqual(offsets, sorted(offsets))
        self.assertLess(offsets[-1], len(data))
        formats = sorted(r["format"] for r in records if r["type"] == th.TYPE_DATETIME)
        self.assertEqual(formats, ["3/2/1", "4:5:6"])
        self.assertTrue({"cpu.percent", "ram.percent", "net.down.mbit"} <= set(compiled.tags))

    def test_number_record_fields(self):
        folder = self.theme(f"""
fonts: {{v: {{file: {FONT}, size: 30, color: "#ff0000"}}}}
widgets:
  - {{type: number, font: v, metric: cpu.percent, digits: 4, align: left, thousands: true, x: 10, y: 20}}
""")
        compiled = th.compile_theme(folder)
        number = [r for r in th.read_records(compiled.data) if r["type"] == th.TYPE_NUMBER][0]
        self.assertEqual((number["align"], number["thousands"], number["opacity"]), (0, 1, 255))
        self.assertEqual(number["colour"], 0xF800)
        self.assertEqual(number["tag"], compiled.tags["cpu.percent"])
        self.assertEqual(len(set(number["widths"][:10])), 1)  # all digits equally wide
        glyph_bytes = number["glyph_height"] * sum(number["widths"])
        self.assertEqual(number["offset"] + glyph_bytes, len(compiled.data))

    def test_tags_skip_the_clock_tag(self):
        metrics = "\n".join(f"  m.{i:02d}: {{command: 'echo {i}'}}" for i in range(25))
        widgets = "\n".join(f"  - {{type: bar, metric: m.{i:02d}, x: 0, y: {i * 10}, width: 50, height: 8}}"
                            for i in range(25))
        compiled = th.compile_theme(self.theme(f"metrics:\n{metrics}\nwidgets:\n{widgets}\n"))
        self.assertNotIn(21, compiled.tags.values())
        self.assertEqual(max(compiled.tags.values()), 26)
        self.assertEqual(len(set(compiled.tags.values())), 25)

    def test_texts_and_rects_cost_no_slot(self):
        compiled = th.compile_theme(self.theme(f"""
fonts: {{l: {{file: {FONT}, size: 20}}}}
widgets:
  - {{type: rect, x: 0, y: 0, width: 100, height: 50, color: "#123456", radius: 6}}
  - {{type: text, font: l, text: "Hallo", x: 5, y: 5}}
"""))
        self.assertEqual(len(th.read_records(compiled.data)), 1)  # only the background
        self.assertEqual(len(compiled.data), th.TABLE_SIZE + th.BACKGROUND_SIZE)

    def test_preview_shows_values_like_the_screen(self):
        compiled = th.compile_theme(self.theme(f"""
background: "#000000"
fonts: {{v: {{file: {FONT}, size: 40, color: "#ffffff"}}}}
widgets:
  - {{type: number, font: v, metric: cpu.percent, digits: 3, x: 10, y: 10}}
  - {{type: bar, metric: ram.percent, x: 10, y: 100, width: 102, height: 10, color: "#00ff00"}}
"""))
        cpu, ram = compiled.tags["cpu.percent"], compiled.tags["ram.percent"]
        empty = th.render_preview(compiled.data, {})
        shown = th.render_preview(compiled.data, {cpu: 42, ram: 50})
        box = (10, 10, 10 + 3 * 23, 58)
        self.assertIsNone(empty.crop(box).getbbox(), "a number without a value shows nothing")
        self.assertIsNotNone(shown.crop(box).getbbox())
        self.assertEqual(empty.getpixel((60, 105))[1] > 200, True, "a bar without a value shows full")
        self.assertEqual(shown.getpixel((40, 105)), (0, 255, 0))
        self.assertEqual(shown.getpixel((80, 105)), (0, 0, 0))

    def test_thousands_text(self):
        self.assertEqual(th.number_text(1234, 1), "1.2")
        self.assertEqual(th.number_text(999, 1), "0.9")
        self.assertEqual(th.number_text(12345, 1), "12")
        self.assertEqual(th.number_text(-1, 0), "-1")

    def test_datetime_formats(self):
        self.assertEqual(th.compile_datetime_format("DD/MM/YYYY HH:mm:ss", "x"), "3/2/1 4:5:6")
        with self.assertRaises(th.ThemeError):
            th.compile_datetime_format("HH u mm", "x")

    def test_refusals(self):
        cases = {
            "no theme.yaml": None,
            "unknown type": "widgets:\n  - {type: circle, x: 0, y: 0}\n",
            "outside the screen": "widgets:\n  - {type: bar, metric: cpu.percent, x: 400, y: 0, width: 100, height: 10}\n",
            "unknown font": "widgets:\n  - {type: number, font: nope, metric: cpu.percent, x: 0, y: 0}\n",
            "bad colour": "background: '#12345g'\n",
            "bad metric": "widgets:\n  - {type: bar, metric: 'CPU %', x: 0, y: 0, width: 10, height: 10}\n",
            "missing font file": "fonts: {a: {file: nope.ttf}}\n",
            "metric without command": "metrics: {weer.temp: {every: 5}}\n",
            "too big": f"fonts: {{h: {{file: {FONT}, size: 200}}}}\nwidgets:\n" + "".join(
                f"  - {{type: number, font: h, metric: m{i}, digits: 1, x: 0, y: 0}}\n" for i in range(8)),
        }
        for name, body in cases.items():
            folder = os.path.join(self.tmp, name.replace(" ", "_"))
            if body is None:
                os.makedirs(folder)
            else:
                write_theme(folder, body)
            with self.assertRaises(th.ThemeError, msg=name) as caught:
                th.compile_theme(folder)
            if name == "too big":
                self.assertIn("te veel", str(caught.exception))


# ---------------------------------------------------------------- metrics

class MetricsTest(unittest.TestCase):
    def test_first_number(self):
        self.assertEqual(first_number("temp: -3.5 C"), -3.5)
        self.assertIsNone(first_number("geen getal"))

    def test_command_metric(self):
        metric = CommandMetric("x", "ignored", every=10, scale=10, runner=lambda c, t: "21.5\n")
        self.assertEqual(metric.poll(now=100.0, wait=True), 215.0)
        calls = []
        metric._runner = lambda c, t: calls.append(c) or "1"
        metric.poll(now=105.0, wait=True)
        self.assertEqual(calls, [], "not run again within 'every'")
        metric.poll(now=111.0, wait=True)
        self.assertEqual(calls, ["ignored"])

    def test_failing_command_leaves_no_value(self):
        def boom(command, timeout):
            raise RuntimeError("exit code 1")
        metric = CommandMetric("x", "boom", runner=boom)
        self.assertIsNone(metric.poll(wait=True))
        self.assertIn("exit code 1", metric.error)

    def test_real_command(self):
        metric = CommandMetric("x", "echo 42", every=1)
        self.assertEqual(metric.poll(wait=True), 42.0)

    def test_read_gives_only_what_was_asked(self):
        metrics = Metrics(gpu_reader=lambda: None)
        values = metrics.read(["cpu.percent", "ram.percent", "gpu.temp"])
        self.assertEqual(set(values), {"cpu.percent", "ram.percent"})
        self.assertTrue(0 <= values["ram.percent"] <= 100)
        self.assertTrue(metrics.known("disk.percent"))
        self.assertFalse(metrics.known("nope"))


# ---------------------------------------------------------------- the program

class ProgramTest(unittest.TestCase):
    def run_program(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "smartmonitor.py"), *args],
                              capture_output=True, text=True, cwd=ROOT, timeout=120)

    def test_compile_default_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "img.dat")
            done = self.run_program("compile", "--out", out)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertGreater(os.path.getsize(out), th.TABLE_SIZE + th.BACKGROUND_SIZE)
            self.assertIn("cpu.percent", done.stdout)

    def test_broken_theme_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_theme(tmp, "widgets:\n  - {type: number, font: nope, metric: cpu.percent, x: 0, y: 0}\n")
            done = self.run_program("compile", "--theme", tmp, "--out", os.path.join(tmp, "x.dat"))
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("Fout in het thema", done.stderr)

    def test_unknown_metric_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_theme(tmp, "widgets:\n  - {type: bar, metric: bestaat.niet, x: 0, y: 0, width: 9, height: 9}\n")
            done = self.run_program("preview", "--theme", tmp, "--out", os.path.join(tmp, "p.png"))
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("bestaat.niet", done.stderr)


if __name__ == "__main__":
    unittest.main()
