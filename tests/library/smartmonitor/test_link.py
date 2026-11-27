# SPDX-License-Identifier: GPL-3.0-or-later
#
# Tests for the arcoloo-minischerm: link (library/smartmonitor/link.py): the Start/Stop buttons in ops.
# Run from the project folder: python -m unittest tests.library.smartmonitor.test_link -v

import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from library.smartmonitor import link  # noqa: E402

# The links the ops page makes (public/js/pclink.js in arcoloo-ops, test/pclink.test.cjs has the same ones).
FROM_OPS = {
    "arcoloo-minischerm://start": ("start", {}),
    "arcoloo-minischerm://opnieuw": ("opnieuw", {}),
    "arcoloo-minischerm://stop": ("stop", {}),
    "arcoloo-minischerm://start?stand=staand&omgedraaid=ja&seconden=10":
        ("start", {"orientation": "portrait", "upside_down": True, "seconds": 10}),
    "arcoloo-minischerm://opnieuw?stand=liggend&omgedraaid=nee":
        ("opnieuw", {"orientation": "landscape", "upside_down": False}),
}


class ParseTest(unittest.TestCase):
    def test_the_links_ops_makes(self):
        for url, expected in FROM_OPS.items():
            self.assertEqual(link.parse(url), expected, url)

    def test_spellings_windows_and_browsers_may_add(self):
        self.assertEqual(link.parse("arcoloo-minischerm://start/"), ("start", {}))
        self.assertEqual(link.parse("ARCOLOO-MINISCHERM:START"), ("start", {}))
        self.assertEqual(link.parse("  arcoloo-minischerm://stop/  "), ("stop", {}))
        self.assertEqual(link.parse("arcoloo-minischerm://start/?seconden=2"), ("start", {"seconds": 2}))

    def test_everything_else_is_refused(self):
        refused = [
            "", None, "https://arcoloomonitoring.up.railway.app/", "arcoloo-minischerm://",
            "arcoloo-minischerm://run", "arcoloo-minischerm://upload", "arcoloo-minischerm://st/art",
            "arcoloo-minischerm://start?url=https://x.org",
            "arcoloo-minischerm://start?key=msk_abc", "arcoloo-minischerm://start?stand=schuin",
            "arcoloo-minischerm://start?omgedraaid=misschien", "arcoloo-minischerm://start?seconden=1",
            "arcoloo-minischerm://start?seconden=121", "arcoloo-minischerm://start?seconden=5.5",
            "arcoloo-minischerm://start?seconden=%C2%B2", "arcoloo-minischerm://start?seconden=-5",
            "arcoloo-minischerm://start?stand=staand&stand=liggend", "arcoloo-minischerm://start?stand",
            "arcoloo-minischerm://stop?stand=staand", "arcoloo-minischerm://start?" + "x" * 300,
            # urlsplit raises a plain ValueError on these (not from a browser, but a .url file or the Run dialog)
            "arcoloo-minischerm://[start", "arcoloo-minischerm://start]", "arcoloo-minischerm://st＃art",
        ]
        for url in refused:
            with self.assertRaises(link.LinkError, msg=url):
                link.parse(url)

    def test_the_reason_cannot_write_own_lines_in_the_log(self):
        for url in ("arcoloo-minischerm://start?a%0D%0Ab%1B%E2%80%AE=1",
                    "arcoloo-minischerm://start?x=1&x%0A=2&x=3", "arcoloo-minischerm://st%0Aart"):
            with self.assertRaises(link.LinkError) as caught:
                link.parse(url)
            self.assertRegex(str(caught.exception), r"^[\x20-\x7e]*$", url)


class Proc:
    def __init__(self, cmdline, cwd=""):
        self.info = {"cmdline": cmdline, "cwd": cwd}


class FindTest(unittest.TestCase):
    def test_only_ops_of_this_folder(self):
        here = os.path.join(tempfile.gettempdir(), "scherm")
        program = os.path.join(here, "smartmonitor.py")
        other = os.path.join(tempfile.gettempdir(), "ander", "smartmonitor.py")
        pythonw = os.path.join(here, ".venv", "Scripts", "pythonw.exe")
        procs = {
            "autostart": Proc([pythonw, program, "ops"]),
            "window": Proc(["python.exe", program, "ops", "--orientation", "staand"]),
            "relative": Proc(["python.exe", "smartmonitor.py", "ops"], cwd=here),
            "other folder": Proc(["python.exe", other, "ops"]),
            "own dashboard": Proc(["python.exe", program]),
            "run": Proc(["python.exe", program, "run"]),
            "dashboard theme named ops": Proc(["python.exe", program, "run", "--theme", "ops"]),
            "preview": Proc(["python.exe", program, "ops-preview"]),
            "hand-typed option first": Proc(["python.exe", program, "--upload", "ops"]),  # not stopped: safe side
            "the handler": Proc([pythonw, program, "link", "arcoloo-minischerm://stop"]),
            "editor": Proc(["notepad.exe", program]),
            "empty": Proc(None),
        }
        found = link.ops_processes(program, list(procs.values()))
        names = sorted(name for name, proc in procs.items() if proc in found)
        self.assertEqual(names, ["autostart", "relative", "window"])

    def test_real_processes_are_found_and_stopped(self):
        with tempfile.TemporaryDirectory() as here:
            program = os.path.join(here, "smartmonitor.py")
            sleep = "import time; time.sleep(60)"
            mine = subprocess.Popen([sys.executable, "-c", sleep, program, "ops"])
            other = subprocess.Popen([sys.executable, "-c", sleep, os.path.join(here, "x", "smartmonitor.py"), "ops"])
            try:
                deadline = time.monotonic() + 10
                found = []
                while not found and time.monotonic() < deadline:
                    found = link.ops_processes(program)
                    time.sleep(0.1)
                time.sleep(1)  # a venv python is a launcher + the interpreter it starts: wait for both
                found = link.ops_processes(program)
                pids = {p.pid for p in found}
                self.assertIn(mine.pid, pids)
                self.assertNotIn(other.pid, pids)
                self.assertEqual(link.stop(found), link.STOPPED)
                self.assertIsNotNone(mine.wait(timeout=10))
                self.assertIsNone(other.poll())
                self.assertEqual(link.stop(link.ops_processes(program)), link.NOT_RUNNING)
            finally:
                for proc in (mine, other):
                    proc.kill()
                    proc.wait()


class Unstoppable:
    """A process started as administrator: we may read its command line, but not stop it or wait on it."""

    def terminate(self):
        import psutil
        raise psutil.AccessDenied(1234)

    def is_running(self):
        return True


class StopTest(unittest.TestCase):
    def test_a_process_that_cannot_be_stopped_is_reported(self):
        import psutil
        with mock.patch.object(psutil, "wait_procs", side_effect=psutil.AccessDenied(1234)):
            self.assertEqual(link.stop([Unstoppable()]), link.STILL_RUNNING)
        with mock.patch.object(psutil, "wait_procs", return_value=([], [Unstoppable()])):
            self.assertEqual(link.stop([Unstoppable()]), link.STILL_RUNNING)
        self.assertEqual(link.stop([]), link.NOT_RUNNING)


class StartTest(unittest.TestCase):
    def test_detached_without_window_and_falls_back_when_the_job_refuses(self):
        calls = []

        def popen(command, creationflags=0, **kwargs):
            calls.append((command, creationflags, kwargs["cwd"]))
            if len(calls) == 1 and os.name == "nt":
                raise OSError("access denied")  # a job that doesn't allow breakaway
            return "started"

        self.assertEqual(link.start("pythonw.exe", "smartmonitor.py", "C:\\scherm", popen=popen), "started")
        self.assertEqual(calls[-1][0], ["pythonw.exe", "smartmonitor.py", "ops"])
        self.assertEqual(calls[-1][2], "C:\\scherm")
        if os.name == "nt":
            self.assertEqual(len(calls), 2)
            self.assertTrue(calls[0][1] & subprocess.CREATE_BREAKAWAY_FROM_JOB)
            self.assertFalse(calls[1][1] & subprocess.CREATE_BREAKAWAY_FROM_JOB)
            self.assertTrue(calls[1][1] & subprocess.DETACHED_PROCESS)


class HandleTest(unittest.TestCase):
    def run_link(self, url, running=False, settings=None, stoppable=True):
        self.settings = dict(settings or {"url": "https://ops.example.org", "key": "msk_" + "a" * 43,
                                          "seconds": 5, "orientation": "landscape", "upside_down": False})
        self.saved, self.events, self.lines = [], [], []
        self.alive = running

        def stop_them(procs):
            self.events.append("stop")
            if not procs:
                return link.NOT_RUNNING
            if not stoppable:
                return link.STILL_RUNNING
            self.alive = False
            return link.STOPPED

        def start_it():
            self.events.append("start")
            self.alive = True

        return link.handle(url, read_settings=lambda: dict(self.settings), write_settings=self.saved.append,
                           running=lambda: ["proc"] if self.alive else [], stop_them=stop_them,
                           start_it=start_it, say=self.lines.append)

    def test_start_when_not_running(self):
        self.assertEqual(self.run_link("arcoloo-minischerm://start"), 0)
        self.assertEqual(self.events, ["stop", "start"])
        self.assertEqual(self.saved, [])
        self.assertIn("gestart", self.lines[-1])

    def test_start_when_running_does_nothing(self):
        self.assertEqual(self.run_link("arcoloo-minischerm://start", running=True), 0)
        self.assertEqual(self.events, [])
        self.assertIn("loopt al", self.lines[-1])

    def test_start_with_new_settings_saves_and_restarts(self):
        url = "arcoloo-minischerm://start?stand=staand&seconden=10"
        self.assertEqual(self.run_link(url, running=True), 0)
        self.assertEqual(self.events, ["stop", "start"])
        self.assertEqual(self.saved[-1]["orientation"], "portrait")
        self.assertEqual(self.saved[-1]["seconds"], 10)
        self.assertEqual(self.saved[-1]["key"], self.settings["key"])  # the key stays as it was
        self.assertIn("opnieuw gestart", self.lines[-1])

    def test_start_with_the_same_settings_does_not_restart(self):
        self.assertEqual(self.run_link("arcoloo-minischerm://start?stand=liggend&seconden=5", running=True), 0)
        self.assertEqual(self.events, [])
        self.assertEqual(self.saved, [])

    def test_opnieuw_and_stop(self):
        self.assertEqual(self.run_link("arcoloo-minischerm://opnieuw", running=True), 0)
        self.assertEqual(self.events, ["stop", "start"])
        self.assertEqual(self.run_link("arcoloo-minischerm://stop", running=True), 0)
        self.assertEqual(self.events, ["stop"])
        self.assertIn("gestopt", self.lines[-1])
        self.assertEqual(self.run_link("arcoloo-minischerm://stop"), 0)
        self.assertIn("liep niet", self.lines[-1])

    def test_refused_link_changes_nothing(self):
        self.assertEqual(self.run_link("arcoloo-minischerm://start?key=msk_x", running=True), 2)
        self.assertEqual((self.events, self.saved), ([], []))
        self.assertIn("geweigerd", self.lines[-1])

    def test_not_set_up_yet(self):
        self.assertEqual(self.run_link("arcoloo-minischerm://start", settings={"url": "", "key": ""}), 1)
        self.assertEqual(self.events, [])
        self.assertIn("eerst instellen", self.lines[-1])
        # with settings: nothing saved either (a half settings file would fool ops-autostart.ps1)
        self.assertEqual(self.run_link("arcoloo-minischerm://start?stand=staand", settings={"url": "", "key": ""}), 1)
        self.assertEqual((self.events, self.saved), ([], []))
        # stopping needs no settings
        self.assertEqual(self.run_link("arcoloo-minischerm://stop", settings={"url": "", "key": ""}), 0)

    def test_running_program_that_cannot_be_stopped(self):
        for url in ("arcoloo-minischerm://stop", "arcoloo-minischerm://opnieuw",
                    "arcoloo-minischerm://start?seconden=9"):
            self.assertEqual(self.run_link(url, running=True, stoppable=False), 1, url)
            self.assertEqual(self.events, ["stop"], url)  # no second program next to it
            self.assertEqual(self.lines[-1], link.CANT_STOP)


class ProgramTest(unittest.TestCase):
    def run_link_command(self, *pairs):
        """smartmonitor.py link with its own log file, not the folder's smartmonitor-ops.log."""
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "ops.log")
            done = subprocess.run([sys.executable, os.path.join(ROOT, "smartmonitor.py"), "link", *pairs],
                                  capture_output=True, text=True, cwd=ROOT, timeout=60,
                                  env=dict(os.environ, SMARTMONITOR_OPS_LOG=log))
            logged = ""
            if os.path.exists(log):
                with open(log, encoding="utf-8") as stream:
                    logged = stream.read()
            return done, logged

    def test_link_command_refuses_a_wrong_link(self):
        for url in ("arcoloo-minischerm://start?url=https://x.org", "arcoloo-minischerm://[start"):
            done, logged = self.run_link_command(url)
            self.assertEqual(done.returncode, 2, done.stderr)
            self.assertIn("geweigerd", done.stdout)
            self.assertIn("geweigerd", logged)

    def test_link_command_wants_exactly_one_link(self):
        done, logged = self.run_link_command("arcoloo-minischerm://stop", "ops")
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("geen link", logged)


if __name__ == "__main__":
    unittest.main()
