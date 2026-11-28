# SPDX-License-Identifier: GPL-3.0-or-later
#
# Tests for "smartmonitor.py ops" (library/smartmonitor/ops.py): Arcoloo ops on the SmartMonitor screen.
# Run from the project folder: python -m unittest tests.library.smartmonitor.test_ops -v

import datetime
import hashlib
import http.server
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from library.smartmonitor import ops, protocol  # noqa: E402
from library.smartmonitor import theme as th  # noqa: E402
from tests.library.smartmonitor.test_smartmonitor import FakeScreen, no_sleep  # noqa: E402

KEY = "msk_" + "A" * 43
MOMENT = datetime.datetime(2026, 11, 26, 9, 41, 7)


def tiles(*statuses):
    statuses = list(statuses) + ["none"] * (8 - len(statuses))
    return [{"key": f"k{i}", "label": label, "status": s}
            for i, (label, s) in enumerate(zip(ops.TILE_LABELS, statuses))]


def target(name, overall="ok", ts=1_000_000, tile_statuses=("ok",)):
    return {"name": name, "kind": "shop", "ts": ts, "overall": overall, "tiles": tiles(*tile_statuses)}


def payload(*targets, now=1_000_000 + 3 * 60_000, stale=4 * 60_000):
    return {"now": now, "staleAfterMs": stale, "targets": list(targets)}


def ok_state(p, fetched_at=0):
    return {"payload": p, "fetched_at": fetched_at, "last_ok_at": fetched_at, "error": None}


# ---------------------------------------------------------------- what the screen shows

class LogicTest(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(ops.norm_seconds(None), 5)
        self.assertEqual(ops.norm_seconds("abc"), 5)
        self.assertEqual(ops.norm_seconds(1), 2)
        self.assertEqual(ops.norm_seconds(500), 120)
        self.assertEqual(ops.norm_seconds("7"), 7)
        self.assertEqual(ops.norm_seconds(7.4), 7)

    def test_names_more_than_ten(self):
        targets = [{"name": f"Winkel {i}"} for i in range(12)]
        names = ops.name_list(targets)
        self.assertEqual(len(names), 10)
        self.assertEqual(names[-1], "Andere winkels")
        self.assertEqual(ops.name_glyph(11, 12), 9)
        self.assertEqual(ops.name_glyph(3, 4), 3)

    def test_rotation_everybody_then_only_red(self):
        three = [target("a"), target("b", "warn"), target("c")]
        self.assertEqual(ops.rotation(three), [0, 1, 2])
        self.assertEqual([ops.current_index(three, t * 1000, 5) for t in (0, 5, 10, 15)], [0, 1, 2, 0])
        three[2]["overall"] = "bad"
        self.assertEqual([ops.current_index(three, t * 1000, 5) for t in (0, 5, 10)], [2, 2, 2])  # stays on the red
        three[0]["overall"] = "bad"
        self.assertEqual([ops.current_index(three, t * 1000, 5) for t in (0, 5, 10)], [0, 2, 0])  # orange skipped
        self.assertEqual(ops.current_index([], 0, 5), -1)

    def test_view_normal_uses_the_server_clock(self):
        state = ok_state(payload(target("Bakkerij", "warn", tile_statuses=("ok", "warn", "bad"))), fetched_at=50_000)
        view = ops.view_for(state, 50_000 + 60_000, 5)  # one more minute since the fetch
        self.assertEqual(view["footer"], ops.FOOTER["normal"])
        self.assertEqual(view["minutes"], 4)
        self.assertEqual(view["lamp"], "warn")
        self.assertEqual(view["tiles"][:4], ["ok", "warn", "bad", None])
        self.assertEqual((view["pos"], view["total"], view["name"]), (1, 1, 0))

    def test_view_stale_pending_empty_denied_offline(self):
        stale = ops.view_for(ok_state(payload(target("a"), stale=60_000)), 0, 5)
        self.assertEqual(stale["footer"], ops.FOOTER["stale"])
        self.assertIsNone(stale["lamp"])
        self.assertEqual(stale["minutes"], 3)
        pending = ops.view_for(ok_state(payload(target("a", "pending", ts=None))), 0, 5)
        self.assertEqual(pending["footer"], ops.FOOTER["pending"])
        self.assertIsNone(pending["minutes"])
        self.assertEqual(ops.view_for(ok_state(payload()), 0, 5)["footer"], ops.FOOTER["empty"])
        self.assertEqual(ops.view_for({"error": "denied"}, 0, 5)["footer"], ops.FOOTER["denied"])
        self.assertEqual(ops.view_for({"payload": None, "last_ok_at": 0, "error": None}, 0, 5)["footer"],
                         ops.FOOTER["pending"])
        recent = {"payload": payload(target("a")), "fetched_at": 0, "last_ok_at": 0, "error": "offline"}
        self.assertEqual(ops.view_for(recent, 60_000, 5)["footer"], ops.FOOTER["normal"])  # 1 min: keep showing
        lost = ops.view_for(recent, ops.OFFLINE_MS + 1, 5)
        self.assertEqual(lost["footer"], ops.FOOTER["offline"])
        self.assertIsNone(lost["lamp"])

    def test_tag_pairs(self):
        view = ops.view_for(ok_state(payload(target("a"), target("b"), target("c"), target("d"), target("e"))),
                            5000, 5)
        pairs = ops.tag_pairs(view, MOMENT)
        values = dict(pairs)
        self.assertEqual(len(pairs), 41)
        self.assertEqual(len(values), 41)  # every tag once
        self.assertNotIn(protocol.CLOCK_TAG, values)
        self.assertEqual([values[t] for t in ops.TAGS["clock"]], [0, 9, 4, 1])
        self.assertEqual([values[t] for t in ops.TAGS["minutes"]], [-1, -1, 3])
        self.assertEqual([values[t] for t in ops.TAGS["pos"]], [-1, 2])
        self.assertEqual([values[t] for t in ops.TAGS["total"]], [5, -1])
        self.assertEqual((values[2], values[3], values[4], values[6]), (1, 0, 0, 0))
        blank = dict(ops.tag_pairs(ops.view_for({"error": "denied"}, 0, 5), MOMENT))
        self.assertEqual(blank[ops.TAGS["unit"]], -1)
        self.assertEqual(blank[ops.TAGS["footer"]], ops.FOOTER["denied"])

    def test_digits(self):
        self.assertEqual(ops.digits_of(None, 3, "right"), [-1, -1, -1])
        self.assertEqual(ops.digits_of(7, 3, "right"), [-1, -1, 7])
        self.assertEqual(ops.digits_of(7, 2, "left"), [7, -1])
        self.assertEqual(ops.digits_of(12345, 3, "right"), [9, 9, 9])

    def test_tile_labels_follow_ops(self):
        self.assertEqual(ops.tile_labels(None), ops.TILE_LABELS)
        renamed = target("a")
        renamed["tiles"][0]["label"] = "Online"
        self.assertEqual(ops.tile_labels(payload(renamed))[0], "Online")
        short = target("a")
        short["tiles"] = short["tiles"][:2]
        self.assertEqual(ops.tile_labels(payload(short)), ops.TILE_LABELS)


# ---------------------------------------------------------------- the theme

def record_of(data, tag):
    return next(r for r in th.read_records(data) if r["tag"] == tag and r["type"] == th.TYPE_NUMBER)


def pixel(picture, record):
    return picture.getpixel((record["x"] + record["width"] // 2, record["y"] + record["height"] // 2))


class ThemeTest(unittest.TestCase):
    NAMES = ["Bakkerij Dirix", "Drinks & Drive", "ARCOLOO-GW"]

    def test_every_orientation_fits_and_has_every_tag(self):
        tags = {t for t, _ in ops.tag_pairs(ops.TEST_VIEW, MOMENT)}
        for orientation in ops.ORIENTATIONS:
            for flip in (False, True):
                data = ops.build_status_theme(self.NAMES, None, orientation, flip)
                self.assertLessEqual(len(data), th.MAX_THEME_BYTES)
                records = th.read_records(data)
                widgets = [r for r in records if r["type"] != th.TYPE_BACKGROUND]
                self.assertTrue(all(r["type"] == th.TYPE_NUMBER for r in widgets))  # no clock widget: can be turned
                self.assertEqual({r["tag"] for r in widgets}, tags)
                for r in widgets:
                    self.assertLessEqual(r["x"] + r["width"], th.SCREEN_WIDTH)
                    self.assertLessEqual(r["y"] + r["height"], th.SCREEN_HEIGHT)

    def test_lamps_share_one_copy(self):
        data = ops.build_status_theme(self.NAMES)
        records = {r["tag"]: r for r in th.read_records(data) if r["type"] == th.TYPE_NUMBER}
        small = {records[ops.TAGS["tile_base"] + i]["offset"] for i in range(24)}
        big = {records[t]["offset"] for t in (2, 3, 4)}
        self.assertEqual(len(small), 1)
        self.assertEqual(len(big), 1)
        self.assertNotEqual(small, big)

    def test_upside_down_and_portrait_turn_exactly(self):
        values = dict(ops.tag_pairs(ops.TEST_VIEW, MOMENT))
        for orientation in ops.ORIENTATIONS:
            seen = []
            for flip in (False, True):
                turn = ops.Orientation(orientation, flip)
                data = ops.build_status_theme(self.NAMES, None, orientation, flip)
                seen.append(turn.unturn(th.render_preview(data, values, MOMENT)))
            self.assertEqual(seen[0].size, (480, 320) if orientation == "landscape" else (320, 480))
            self.assertEqual(seen[0].tobytes(), seen[1].tobytes(), orientation)

    def test_lamp_on_and_dimmed(self):
        for orientation in ops.ORIENTATIONS:
            data = ops.build_status_theme(self.NAMES, None, orientation, True)
            red = record_of(data, ops.TAGS["lamp_bad"])
            on = pixel(th.render_preview(data, {4: 1}, MOMENT), red)
            dim = pixel(th.render_preview(data, {4: 0}, MOMENT), red)
            self.assertGreater(on[0], 200)
            self.assertLess(on[1], 120)
            self.assertLess(dim[0], 100)

    def test_minus_one_shows_nothing(self):
        data = ops.build_status_theme(self.NAMES, None, "portrait")
        empty = th.render_preview(data, {}, MOMENT)
        blank = th.render_preview(data, {t: -1 for t in ops.TAGS["minutes"] + ops.TAGS["pos"] + [6]}, MOMENT)
        self.assertEqual(empty.tobytes(), blank.tobytes())
        shown = th.render_preview(data, {ops.TAGS["minutes"][2]: 8}, MOMENT)
        self.assertNotEqual(empty.tobytes(), shown.tobytes())

    def test_many_long_names_still_fit(self):
        names = ops.name_list([{"name": f"Winkel nummer {i} met een heel lange naam"} for i in range(14)])
        for orientation in ops.ORIENTATIONS:
            self.assertLessEqual(len(ops.build_status_theme(names, None, orientation)), th.MAX_THEME_BYTES)

    def test_compile_refuses_bad_widgets(self):
        turn = ops.Orientation()
        background = ops.Image.new("RGB", (480, 320))
        font = ops.DigitFont(ops.FONT_BOLD, 20)
        with self.assertRaises(th.ThemeError):
            ops.compile_widgets(background, (0, 0, 0), [{"type": "number", "tag": 21, "x": 0, "y": 0, "font": font,
                                                         "colour": (255, 255, 255)}], turn)
        with self.assertRaises(th.ThemeError):
            ops.compile_widgets(background, (0, 0, 0), [{"type": "number", "tag": 1, "x": 475, "y": 0, "font": font,
                                                         "colour": (255, 255, 255)}], turn)
        lamp = {"0": ops.lamp_mask(10, 1)}
        with self.assertRaises(th.ThemeError):
            ops.compile_widgets(background, (0, 0, 0), [
                {"type": "picture", "tag": 1, "x": 0, "y": 0, "width": 10, "height": 10, "colour": (9, 9, 9),
                 "glyphs": lamp, "share": "x"},
                {"type": "picture", "tag": 2, "x": 0, "y": 20, "width": 12, "height": 12, "colour": (9, 9, 9),
                 "glyphs": {"0": ops.lamp_mask(12, 1)}, "share": "x"}], turn)
        with self.assertRaises(th.ThemeError):
            ops.compile_widgets(ops.Image.new("RGB", (320, 480)), (0, 0, 0), [], turn)
        with self.assertRaises(ValueError):
            ops.Orientation("diagonal")


# ---------------------------------------------------------------- the connection with ops

class Handler(http.server.BaseHTTPRequestHandler):
    body = payload(target("Bakkerij"))
    seen_keys = []

    def do_GET(self):  # noqa: N802 - http.server API
        Handler.seen_keys.append(self.headers.get("x-display-key"))
        if self.path == "/elders/api/display/status":
            self.send_response(200)
            self.end_headers()
            return
        if self.path != ops.STATUS_PATH:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/elders{ops.STATUS_PATH}")
            self.end_headers()
            return
        if self.headers.get("x-display-key") != KEY:
            self.send_response(401)
            self.end_headers()
            return
        data = json.dumps(Handler.body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class NetworkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_fetch_with_key(self):
        status, body = ops.fetch_status(self.base, KEY)
        self.assertEqual(status, 200)
        self.assertEqual(body["targets"][0]["name"], "Bakkerij")
        self.assertEqual(Handler.seen_keys[-1], KEY)
        self.assertEqual(ops.fetch_status(self.base, "msk_" + "B" * 43), (401, None))

    def test_redirect_is_not_followed(self):
        before = len(Handler.seen_keys)
        status, body = ops.fetch_status(self.base + "/ander", KEY)
        self.assertEqual((status, body), (302, None))
        self.assertEqual(len(Handler.seen_keys), before + 1)  # the key never went to the redirect target

    def test_no_connection(self):
        self.assertEqual(ops.fetch_status("http://127.0.0.1:9", KEY, timeout=2), (0, None))

    def test_addresses_and_keys(self):
        self.assertEqual(ops.normalize_url(" https://ops.example.org/minischerm.html "), "https://ops.example.org")
        self.assertEqual(ops.normalize_url("http://localhost:4430/"), "http://localhost:4430")
        for wrong in ("", "ops.example.org", "http://ops.example.org", "ftp://ops.example.org"):
            with self.assertRaises(ValueError, msg=wrong):
                ops.normalize_url(wrong)
        self.assertEqual(ops.check_key(" " + KEY + " "), KEY)
        for wrong in ("", "msk_kort", "abc_" + "A" * 43):
            with self.assertRaises(ValueError):
                ops.check_key(wrong)
        self.assertEqual(ops.masked(KEY), "msk_AAAA…")

    def test_poller_states(self):
        answers = [(200, payload(target("a"))), (500, None), (401, None), (0, None), (200, payload())]
        now = [1000.0]
        poller = ops.Poller("https://x", KEY, fetch=lambda url, key: answers.pop(0), clock=lambda: now[0])
        poller.poll_once()
        self.assertEqual(poller.snapshot()["payload"]["targets"][0]["name"], "a")
        poller.poll_once()
        self.assertEqual(poller.snapshot()["error"], "offline")
        self.assertIsNotNone(poller.snapshot()["payload"])  # keeps the last status
        poller.poll_once()
        self.assertEqual(poller.snapshot()["error"], "denied")
        self.assertIsNone(poller.snapshot()["payload"])
        poller.poll_once()
        self.assertEqual(poller.snapshot()["error"], "offline")
        now[0] = 2000.0
        poller.poll_once()
        self.assertEqual(poller.snapshot(), {"payload": payload(), "fetched_at": 2_000_000.0,
                                             "last_ok_at": 2_000_000.0, "error": None})


# ---------------------------------------------------------------- driving the screen

class Store:
    def __init__(self, digest=None, names=None):
        self.digest, self.names, self.labels = digest, names, None

    def get_digest(self):
        return self.digest

    def set_digest(self, digest, names, labels):
        self.digest, self.names, self.labels = digest, names, labels

    def last_names(self):
        return self.names

    def last_labels(self):
        return self.labels


class CountingScreen(FakeScreen):
    def __init__(self):
        super().__init__()
        self.value_reports = 0

    def write(self, report):
        if self.mode == "idle" and report[0] == 0x02:
            self.value_reports += 1
        super().write(report)


class FixedPoller:
    def __init__(self, state):
        self.state = state

    def snapshot(self):
        return dict(self.state)


def fast_upload(transport, data, progress):
    protocol.upload_theme(transport, data, progress, sleep=no_sleep)


class ScreenTest(unittest.TestCase):
    def make(self, state, store=None, **options):
        fake, store = CountingScreen(), store or Store()
        screen = ops.OpsScreen(fake, FixedPoller(state), store, say=lambda line: None, upload=fast_upload, **options)
        return fake, store, screen

    def test_first_tick_uploads_then_only_values(self):
        state = ok_state(payload(target("Bakkerij", "bad"), target("Drinks")))
        fake, store, screen = self.make(state, orientation="portrait", upside_down=True)
        screen.tick(MOMENT, now_ms=0)
        self.assertTrue(fake.finished)
        self.assertEqual(fake.theme(), screen.theme)
        self.assertEqual(store.digest, hashlib.sha256(screen.theme).hexdigest())
        self.assertEqual(store.names, ["Bakkerij", "Drinks"])
        self.assertEqual(fake.values[ops.TAGS["lamp_bad"]], 1)
        self.assertEqual(fake.values[ops.TAGS["name"]], 0)
        self.assertIsNotNone(fake.clock)
        reopens = fake.reopens
        for second in range(1, 10):
            screen.tick(MOMENT, now_ms=second * 1000)
        self.assertEqual(fake.reopens, reopens)  # no second upload
        self.assertEqual(fake.value_reports, 3)  # 41 pairs = 3 reports, only once: nothing changed
        screen.tick(MOMENT, now_ms=10_000)
        self.assertEqual(fake.value_reports, 6)  # every 10 s anyway

    def test_new_shop_puts_a_new_theme_on_the_screen(self):
        state = ok_state(payload(target("a")))
        fake, store, screen = self.make(state)
        screen.tick(MOMENT, now_ms=0)
        first = store.digest
        screen.poller.state = ok_state(payload(target("a"), target("b")))
        screen.tick(MOMENT, now_ms=1000)
        self.assertNotEqual(store.digest, first)
        self.assertEqual(fake.theme(), screen.theme)

    def test_theme_already_on_the_screen(self):
        data = ops.build_status_theme(["a"], None, "landscape", False)
        store = Store(hashlib.sha256(data).hexdigest(), ["a"])
        fake, _, screen = self.make(ok_state(payload(target("a"))), store)
        screen.tick(MOMENT, now_ms=0)
        self.assertFalse(fake.finished)
        self.assertEqual(fake.reopens, 0)
        # no answer from ops yet: the names of the last theme, so no needless upload
        fake, _, screen = self.make({"payload": None, "last_ok_at": 0, "error": "offline"}, store)
        screen.tick(MOMENT, now_ms=0)
        self.assertFalse(fake.finished)
        self.assertEqual(fake.values[ops.TAGS["footer"]], ops.FOOTER["pending"])

    def test_upload_stopped_halfway_is_done_again(self):
        # liggend on the screen, then staand starts uploading and is stopped (Opnieuw in ops), then liggend again
        store = Store()
        fake, _, screen = self.make(ok_state(payload(target("a"))), store)
        screen.tick(MOMENT, now_ms=0)

        def stopped_halfway(transport, data, progress):
            raise KeyboardInterrupt  # what terminate() looks like from the inside: no time to clean up

        _, _, screen = self.make(ok_state(payload(target("a"))), store, orientation="portrait")
        screen.upload = stopped_halfway
        with self.assertRaises(KeyboardInterrupt):
            screen.tick(MOMENT, now_ms=0)
        self.assertIsNone(store.digest)  # nobody knows what is on the screen now
        fake, _, screen = self.make(ok_state(payload(target("a"))), store)
        screen.tick(MOMENT, now_ms=0)
        self.assertTrue(fake.finished)  # so liggend goes on it again

    def test_turning_the_screen_uploads_again(self):
        store = Store()
        fake, _, screen = self.make(ok_state(payload(target("a"))), store)
        screen.tick(MOMENT, now_ms=0)
        landscape = store.digest
        fake, _, screen = self.make(ok_state(payload(target("a"))), store, orientation="landscape", upside_down=True)
        screen.tick(MOMENT, now_ms=0)
        self.assertTrue(fake.finished)
        self.assertNotEqual(store.digest, landscape)


# ---------------------------------------------------------------- the program

def load_program():
    spec = importlib.util.spec_from_file_location("smartmonitor_program", os.path.join(ROOT, "smartmonitor.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProgramTest(unittest.TestCase):
    def run_program(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "smartmonitor.py"), *args],
                              capture_output=True, text=True, cwd=ROOT, timeout=120)

    def test_preview_test_picture_portrait_upside_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "ops.png")
            done = self.run_program("ops-preview", "--test", "--orientation", "staand", "--upside-down", "ja",
                                    "--out", out)
            self.assertEqual(done.returncode, 0, done.stderr)
            with ops.Image.open(out) as picture:
                self.assertEqual(picture.size, (320, 480))
            self.assertIn("portrait, omgedraaid", done.stdout)

    def test_wrong_settings_are_refused_and_not_saved(self):
        program = load_program()
        before = os.path.exists(program.OPS_FILE)
        done = self.run_program("ops", "--url", "http://ops.example.org", "--key", KEY)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("https://", done.stderr)
        self.assertEqual(os.path.exists(program.OPS_FILE), before)
        done = self.run_program("ops", "--key", "msk_kort")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("msk_", done.stderr)

    def test_one_program_at_a_time(self):
        program = load_program()
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "smartmonitor.py"])
        try:
            with tempfile.TemporaryDirectory() as tmp:
                program.LOCK_FILE = os.path.join(tmp, "smartmonitor.lock")
                with open(program.LOCK_FILE, "w", encoding="utf-8") as stream:
                    stream.write(str(sleeper.pid))
                lines = []
                with self.assertRaises(SystemExit):
                    program.take_lock(lines.append)
                self.assertIn(str(sleeper.pid), lines[0])
                with open(program.LOCK_FILE, "w", encoding="utf-8") as stream:
                    stream.write("999999")  # a program that stopped without cleaning up
                program.take_lock(lines.append)
                with open(program.LOCK_FILE, encoding="utf-8") as stream:
                    self.assertEqual(stream.read(), str(os.getpid()))
        finally:
            sleeper.kill()
            sleeper.wait()


if __name__ == "__main__":
    unittest.main()
