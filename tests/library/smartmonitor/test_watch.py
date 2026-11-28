# SPDX-License-Identifier: GPL-3.0-or-later
#
# Tests for two programs on one screen: library/smartmonitor/watch.py and after_write_failure in smartmonitor.py.
# Run from the project folder: python -m unittest tests.library.smartmonitor.test_watch -v

import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from library.smartmonitor import watch  # noqa: E402

LOOKS_PER_S = round(1 / watch.LOOK_S)


def seconds(n, look):
    return look * round(n * LOOKS_PER_S)


class Screen:
    """What the program sees at every look: 'X' = plugged in, '.' = gone; the last look repeats forever.
    The clock moves with the looks (one look = LOOK_S), so the string is a timeline."""

    def __init__(self, looks):
        self.looks, self.index, self.now = looks, 0, 0.0

    def present(self):
        look = self.looks[min(self.index, len(self.looks) - 1)]
        self.index += 1
        return look == "X"

    def sleep(self, pause):
        self.now += pause

    def clock(self):
        return self.now

    def wait(self):
        watch.wait_after_failure(self.present, sleep=self.sleep, clock=self.clock)
        return self.now


class WatchTest(unittest.TestCase):
    def test_another_program_uploads_a_theme(self):
        # Gone when we look (restarting into its upload mode), back for the upload, gone again, back with its theme.
        upload = seconds(17, "X") + seconds(1, ".") + "X"
        with self.assertRaises(watch.ForeignUpload):
            Screen(seconds(1, ".") + upload).wait()
        # Our write failed on the old handle when the screen was already back in its upload mode.
        with self.assertRaises(watch.ForeignUpload):
            Screen(upload).wait()

    def test_cable_out_and_back_in(self):
        self.assertAlmostEqual(Screen(seconds(5, ".") + "X").wait(), 5 + watch.QUIET_S, delta=0.5)
        # Windows still listed the screen for a moment after it was pulled out: the same restart, not a second one.
        self.assertAlmostEqual(Screen("X" + seconds(5, ".") + "X").wait(), 5 + watch.QUIET_S, delta=0.5)

    def test_wobbly_plug_is_one_restart(self):
        looks = seconds(1, ".") + seconds(1, "X") + seconds(1, ".") + "X"
        self.assertAlmostEqual(Screen(looks).wait(), 3 + watch.QUIET_S, delta=0.5)

    def test_screen_never_left(self):
        self.assertAlmostEqual(Screen("X").wait(), watch.QUIET_S, delta=0.5)

    def test_long_gone_keeps_waiting(self):
        self.assertAlmostEqual(Screen(seconds(600, ".") + "X").wait(), 600 + watch.QUIET_S, delta=0.5)

    def test_back_for_a_while_then_gone_again_counts(self):
        # Twice pulled out within QUIET_S looks the same as an upload by another program: that is the price.
        with self.assertRaises(watch.ForeignUpload):
            Screen(seconds(2, ".") + seconds(10, "X") + seconds(2, ".") + "X").wait()


def load_program():
    spec = importlib.util.spec_from_file_location("smartmonitor_program", os.path.join(ROOT, "smartmonitor.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Transport:
    def __init__(self):
        self.closed = self.opened = 0

    def close(self):
        self.closed += 1

    def open(self):
        self.opened += 1


class AfterWriteFailureTest(unittest.TestCase):
    def setUp(self):
        self.program = load_program()
        self.tmp = tempfile.TemporaryDirectory()
        self.program.STATE_FILE = os.path.join(self.tmp.name, "smartmonitor-state.json")
        self.program.write_state({"uploaded_sha256": "abc", "theme": "ops", "ops_names": ["Bakkerij"]})
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def state(self):
        with open(self.program.STATE_FILE, encoding="utf-8") as stream:
            return json.load(stream)

    def test_other_program_uploaded_stops_and_forgets_our_theme(self):
        def wait(present):
            raise watch.ForeignUpload()
        transport = Transport()
        with self.assertRaises(SystemExit) as stopped:
            self.program.after_write_failure(transport, OSError("weg"), 1, say=self.lines.append, wait=wait)
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(self.state(), {"uploaded_sha256": None, "theme": "ops", "ops_names": ["Bakkerij"]},
                         "the next start puts its own theme back")
        self.assertIn("Even niets sturen", self.lines[0])
        self.assertIn("ander programma zette er zijn eigen thema op", self.lines[-1])
        self.assertEqual((transport.closed, transport.opened), (1, 0), "closed, never opened again")

    def test_quiet_again_carries_on(self):
        waited = []
        transport = Transport()
        for failures in (1, 2, 5):
            self.program.after_write_failure(transport, OSError("weg"), failures, say=self.lines.append,
                                             wait=waited.append, present=lambda: True)
        self.assertEqual(len(waited), 3, "waits every time before writing again")
        self.assertEqual((transport.closed, transport.opened), (3, 3))
        self.assertEqual(self.state()["uploaded_sha256"], "abc", "our theme is still on it")
        self.assertEqual(len(self.lines), 2, "the long message once, then the hint at the 5th failure")
        self.assertIn("Trek het schermpje even uit", self.lines[1])


if __name__ == "__main__":
    unittest.main()
