# SPDX-License-Identifier: GPL-3.0-or-later
#
# Arcoloo ops on the SmartMonitor screen ("smartmonitor.py ops"): every shop and the gateway in turn, every N seconds;
# when something is RED the screen only shows the red one(s). The status comes from the ops dashboard
# (GET /api/display/status) with a read-only screen key (x-display-key, "msk_..."): names and a status per tile, no
# details. Same screen as the browser page /minischerm.html of arcoloo-ops, but without a browser.
#
# The screen only plays pre-drawn pictures and numbers, so every name, text and lamp is a picture widget: a number
# record of one glyph whose glyph n is a picture (value n shows picture n, value -1 shows nothing). Every widget shows
# exactly one glyph, the clock included (four digit widgets), so the whole theme can be turned: landscape or portrait,
# upside down or not.

import functools
import hashlib
import json
import math
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

from library.smartmonitor import protocol
from library.smartmonitor.theme import (BACKGROUND_SIZE, CLOCK_TAG, MAX_THEME_BYTES, MAX_WIDGET_RECORDS, NUMBER_GLYPHS,
                                        REPO_ROOT, SCREEN_HEIGHT, SCREEN_WIDTH, TABLE_SIZE, TYPE_BACKGROUND,
                                        TYPE_NUMBER, ThemeError, head, rgb565, rgb_plane, slot)

# ---------------------------------------------------------------- what the screen shows (same as minischerm.js)

# Same order and names as display.js TILES of arcoloo-ops; the labels of the status replace these when they come.
TILE_LABELS = ["Bereikbaar", "Certificaat", "Backup", "Licentie", "Beveiliging", "Bonprinter", "Facturatie",
               "Onderhoud"]
# A picture widget has 10 pictures (values 0-9): that many names fit. With more shops the 10th is "Andere winkels" and
# the counter (11/12) tells which one it is.
MAX_NAMES = 10
# Tags (21 is the clock of the screen). Tile i: green 50+3i, orange 51+3i, red 52+3i.
TAGS = {"name": 1, "lamp_ok": 2, "lamp_warn": 3, "lamp_bad": 4, "footer": 5, "unit": 6,
        "clock": [30, 31, 32, 33], "minutes": [40, 41, 42], "pos": [43, 44], "total": [45, 46], "tile_base": 50}
FOOTER = {"normal": 0, "stale": 1, "pending": 2, "offline": 3, "denied": 4, "empty": 5}
FOOTER_TEXT = ["Laatste controle", "Verouderd! Laatste controle", "Nog geen gegevens", "Geen verbinding met ops",
               "Sleutel ongeldig", "Geen winkels in ops"]
OFFLINE_MS = 3 * 60 * 1000  # this long without an answer from ops -> "Geen verbinding met ops"
POLL_S = 30
DEFAULT_SECONDS, MIN_SECONDS, MAX_SECONDS = 5, 2, 120
LIGHTS = ("ok", "warn", "bad")
ORIENTATIONS = ("landscape", "portrait")
KEY_RE = re.compile(r"^msk_[A-Za-z0-9_-]{43}$")
STATUS_PATH = "/api/display/status"


def norm_seconds(value):
    """Seconds per shop: 2-120, default 5."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_SECONDS
    return max(MIN_SECONDS, min(MAX_SECONDS, number))


def targets_of(payload):
    targets = (payload or {}).get("targets") if isinstance(payload, dict) else None
    return [t for t in targets if isinstance(t, dict)] if isinstance(targets, list) else []


def name_list(targets):
    """The names in the theme (picture i = shop i)."""
    names = [str(t.get("name") or "?") for t in targets]
    return names if len(names) <= MAX_NAMES else names[:MAX_NAMES - 1] + ["Andere winkels"]


def name_glyph(index, total):
    return index if total <= MAX_NAMES else min(index, MAX_NAMES - 1)


def tile_labels(payload):
    """The tile labels of ops (the first target with all tiles), else the built-in ones."""
    for target in targets_of(payload):
        tiles = target.get("tiles")
        if isinstance(tiles, list) and len(tiles) >= len(TILE_LABELS):
            labels = [str((tile or {}).get("label") or "").strip()[:24] for tile in tiles[:len(TILE_LABELS)]]
            if all(labels):
                return labels
    return list(TILE_LABELS)


def rotation(targets):
    """Who comes in turn: everybody; when something is red, only the red ones."""
    bad = [i for i, t in enumerate(targets) if t.get("overall") == "bad"]
    return bad or list(range(len(targets)))


def current_index(targets, now_ms, seconds):
    """Who now: by the clock, so it keeps turning without remembering where it was."""
    order = rotation(targets)
    if not order:
        return -1
    return order[int(now_ms // (norm_seconds(seconds) * 1000)) % len(order)]


def view_for(state, now_ms, seconds):
    """state = {payload, fetched_at, last_ok_at, error: None | 'offline' | 'denied'} -> what the screen shows now."""
    state = state or {}
    view = {"index": -1, "name": -1, "lamp": None, "tiles": [None] * len(TILE_LABELS), "minutes": None,
            "footer": FOOTER["pending"], "unit": False, "pos": None, "total": None, "red": False}
    if state.get("error") == "denied":
        view["footer"] = FOOTER["denied"]
        return view
    payload = state.get("payload")
    lost = state.get("error") == "offline" and now_ms - (state.get("last_ok_at") or 0) > OFFLINE_MS
    if not isinstance(payload, dict):
        view["footer"] = FOOTER["offline"] if lost else FOOTER["pending"]
        return view
    targets = targets_of(payload)
    if not targets:
        view["footer"] = FOOTER["offline"] if lost else FOOTER["empty"]
        return view
    index = current_index(targets, now_ms, seconds)
    target = targets[index]
    view.update(index=index, name=name_glyph(index, len(targets)), pos=index + 1, total=len(targets),
                red=any(t.get("overall") == "bad" for t in targets))
    if lost:
        view["footer"] = FOOTER["offline"]
        return view
    # Age by the clock of the server (+ the time since we asked): a PC clock that is wrong does not matter.
    ts, server_now = target.get("ts"), payload.get("now")
    if not isinstance(ts, (int, float)) or not isinstance(server_now, (int, float)) or \
            target.get("overall") not in LIGHTS:
        return view  # not checked yet
    age = max(0, (server_now - ts) + (now_ms - (state.get("fetched_at") or now_ms)))
    view["minutes"] = int(age // 60000)
    view["unit"] = True
    stale_after = payload.get("staleAfterMs")
    if isinstance(stale_after, (int, float)) and age > stale_after:
        view["footer"] = FOOTER["stale"]
        return view
    view["footer"] = FOOTER["normal"]
    view["lamp"] = target["overall"]
    for k, tile in enumerate((target.get("tiles") or [])[:len(TILE_LABELS)]):
        status = tile.get("status") if isinstance(tile, dict) else None
        view["tiles"][k] = status if status in LIGHTS else None
    return view


def digits_of(number, count, mode):
    """A number as separate digits on `count` places (every place its own widget of one digit); -1 = empty.
    mode 'right' (empty places in front) or 'left'."""
    if number is None:
        return [-1] * count
    text = str(max(0, int(number)))
    if len(text) > count:
        text = "9" * count
    digits, blanks = [int(c) for c in text], [-1] * (count - len(text))
    return digits + blanks if mode == "left" else blanks + digits


def tag_pairs(view, moment):
    """The numbers for the screen (41 pairs -> three reports). moment: the local time for the clock digits."""
    def on(status, want):
        return 1 if status == want else 0
    lamp = view["lamp"]
    pairs = [(TAGS["name"], view["name"]), (TAGS["lamp_ok"], on(lamp, "ok")), (TAGS["lamp_warn"], on(lamp, "warn")),
             (TAGS["lamp_bad"], on(lamp, "bad")), (TAGS["footer"], view["footer"]),
             (TAGS["unit"], 0 if view["unit"] else -1)]
    for i, status in enumerate(view["tiles"]):
        base = TAGS["tile_base"] + 3 * i
        pairs += [(base, on(status, "ok")), (base + 1, on(status, "warn")), (base + 2, on(status, "bad"))]
    pairs += list(zip(TAGS["minutes"], digits_of(view["minutes"], 3, "right")))
    pairs += list(zip(TAGS["pos"], digits_of(view["pos"], 2, "right")))
    pairs += list(zip(TAGS["total"], digits_of(view["total"], 2, "left")))
    pairs += list(zip(TAGS["clock"], [moment.hour // 10, moment.hour % 10, moment.minute // 10, moment.minute % 10]))
    return pairs


# Test picture: every kind of lamp, the first name and all digits.
TEST_VIEW = {"index": 0, "name": 0, "lamp": "bad", "tiles": ["ok", "warn", "bad", "ok", "warn", "bad", "ok", None],
             "minutes": 123, "footer": FOOTER["normal"], "unit": True, "pos": 12, "total": 34, "red": True}


# ---------------------------------------------------------------- drawing the theme

FONT_BOLD = REPO_ROOT + "/res/fonts/roboto/Roboto-Bold.ttf"
FONT_MEDIUM = REPO_ROOT + "/res/fonts/roboto/Roboto-Medium.ttf"
COLOURS = {"navy": (0x1e, 0x3a, 0x5f), "housing": (0x12, 0x24, 0x3b), "cream": (0xfa, 0xf7, 0xf2),
           "soft": (0xc9, 0xd4, 0xe4), "white": (0xff, 0xff, 0xff), "line": (0xe3, 0xdc, 0xd0),
           "green": (0x34, 0xd0, 0x6c), "orange": (0xff, 0xa8, 0x24), "red": (0xf0, 0x48, 0x48)}
DIM = 0.22  # a dimmed lamp: its colour at 22 %
SUPERSAMPLE = 3  # the background is drawn 3x as big and scaled down: smooth rounded corners


def rnd(value):
    """Round half up, like Math.round."""
    return int(math.floor(value + 0.5))


@functools.lru_cache(maxsize=64)
def font_at(path, size):
    return ImageFont.truetype(path, size)


class DigitFont:
    """A font as digit cells: every glyph of a number widget is a cell of the same size (digit_width x height), so a
    widget can be turned. '.' and '-' are empty cells: value -1 then shows nothing in a box of one digit (the '1'
    falls outside the box and is clipped; confirmed on the screen)."""

    def __init__(self, path, size):
        self.font = font_at(path, size)
        ascent = descent = 0
        for character in "0123456789.-:/":
            _, top, _, bottom = self.font.getbbox(character, anchor="ls")
            ascent, descent = max(ascent, -top), max(descent, bottom)
        self.ascent = math.ceil(ascent) + 1
        self.height = self.ascent + math.ceil(descent) + 1
        self.digit_width = max(self.advance(d) for d in "0123456789")

    def advance(self, character):
        return max(1, math.ceil(self.font.getlength(character)))

    def mask(self, character, width, x=0):
        picture = Image.new("L", (width, self.height), 0)
        ImageDraw.Draw(picture).text((x, self.ascent), character, font=self.font, fill=255, anchor="ls")
        return picture

    def cell(self, character):
        if character.isdigit():
            return self.mask(character, self.digit_width, (self.digit_width - self.advance(character)) // 2)
        return Image.new("L", (self.digit_width, self.height), 0)


def text_mask(width, height, text, path, max_px, min_px, lines=1, align="left", pad=0):
    """Text as an alpha mask width x height: words over at most `lines` rows, as big as possible (max_px -> min_px),
    centred vertically; otherwise the last row shortened with an ellipsis."""
    inner_w, inner_h, words = width - 2 * pad, height - 2 * pad, str(text).split(" ")

    def fits(font, row):
        return font.getlength(row) <= inner_w

    def wrap(font):
        rows = []
        for word in words:
            if rows and fits(font, rows[-1] + " " + word):
                rows[-1] += " " + word
            else:
                rows.append(word)
        return rows

    px = max_px
    while px >= min_px:
        font = font_at(path, px)
        rows = wrap(font)
        if len(rows) <= lines and len(rows) * rnd(px * 1.15) <= inner_h and all(fits(font, r) for r in rows):
            break
        px -= 1
    else:
        px = min_px
        font = font_at(path, px)
        rows = wrap(font)
    line_height = rnd(px * 1.15)
    most = max(1, min(lines, inner_h // line_height))
    if len(rows) > most:
        rows = rows[:most]
        rows[-1] += "…"

    def shorten(row):
        if fits(font, row):
            return row
        while len(row) > 1 and not fits(font, row + "…"):
            row = row[:-1]
        return row.rstrip("…").rstrip() + "…"

    rows = [shorten(r) for r in rows]
    picture = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(picture)
    x = width - pad if align == "right" else pad
    top = pad + (inner_h - len(rows) * line_height) / 2
    for i, row in enumerate(rows):
        draw.text((x, rnd(top + (i + 0.5) * line_height)), row, font=font, fill=255,
                  anchor="rm" if align == "right" else "lm")
    return picture


def lamp_mask(diameter, alpha):
    """A round lamp diameter x diameter with a soft edge; alpha = strength (dimmed or on)."""
    scale = 4
    big = Image.new("L", (diameter * scale, diameter * scale), 0)
    ImageDraw.Draw(big).ellipse([scale // 2, scale // 2, diameter * scale - scale // 2 - 1,
                                 diameter * scale - scale // 2 - 1], fill=255)
    return big.resize((diameter, diameter), Image.LANCZOS).point(lambda v: rnd(v * alpha))


class Painter:
    """The background in design coordinates (landscape 480x320 or portrait 320x480), drawn big and scaled down."""

    def __init__(self, size, fill):
        self.size = size
        self.image = Image.new("RGB", (size[0] * SUPERSAMPLE, size[1] * SUPERSAMPLE), fill)
        self.draw = ImageDraw.Draw(self.image)
        self.glyphs = []

    def _box(self, x, y, width, height):
        s = SUPERSAMPLE
        return [rnd(x * s), rnd(y * s), rnd((x + width) * s) - 1, rnd((y + height) * s) - 1]

    def rect(self, x, y, width, height, colour):
        self.draw.rectangle(self._box(x, y, width, height), fill=colour)

    def round_rect(self, x, y, width, height, radius, fill, outline=None):
        self.draw.rounded_rectangle(self._box(x, y, width, height), radius=rnd(radius * SUPERSAMPLE), fill=fill,
                                    outline=outline, width=SUPERSAMPLE if outline else 0)

    def text(self, text, x, y, path, size, colour, align="left"):
        anchor = {"left": "lm", "center": "mm", "right": "rm"}[align]
        self.draw.text((rnd(x * SUPERSAMPLE), rnd(y * SUPERSAMPLE)), text, font=font_at(path, size * SUPERSAMPLE),
                       fill=colour, anchor=anchor)

    def glyph(self, mask, x, y, colour):
        """A mask pasted after scaling down (pixel exact, e.g. the ':' of the clock next to its digit widgets)."""
        self.glyphs.append((mask, x, y, colour))

    def finish(self):
        picture = self.image.resize(self.size, Image.LANCZOS)
        for mask, x, y, colour in self.glyphs:
            picture.paste(Image.new("RGB", mask.size, colour), (x, y), mask)
        return picture


class Orientation:
    """How the design is put on the panel (480x320). Landscape: as drawn, upside down = turned 180 degrees. Portrait:
    the design is 320x480 and turned a quarter (upside down = the other quarter). Every widget shows one glyph, so a
    widget is turned by turning its box and its glyphs."""

    def __init__(self, orientation="landscape", upside_down=False):
        if orientation not in ORIENTATIONS:
            raise ValueError(f"orientation is landscape or portrait, not {orientation!r}")
        self.portrait = orientation == "portrait"
        self.upside_down = bool(upside_down)
        self.size = (SCREEN_HEIGHT, SCREEN_WIDTH) if self.portrait else (SCREEN_WIDTH, SCREEN_HEIGHT)
        if self.portrait:
            self.op = Image.Transpose.ROTATE_270 if self.upside_down else Image.Transpose.ROTATE_90
        else:
            self.op = Image.Transpose.ROTATE_180 if self.upside_down else None

    def image(self, picture):
        return picture if self.op is None else picture.transpose(self.op)

    def unturn(self, picture):
        """A picture of the panel back as the viewer sees it (previews)."""
        undo = {None: None, Image.Transpose.ROTATE_180: Image.Transpose.ROTATE_180,
                Image.Transpose.ROTATE_90: Image.Transpose.ROTATE_270,
                Image.Transpose.ROTATE_270: Image.Transpose.ROTATE_90}[self.op]
        return picture if undo is None else picture.transpose(undo)

    def box(self, x, y, width, height):
        design_w, design_h = self.size
        if self.op is None:
            return x, y, width, height
        if self.op == Image.Transpose.ROTATE_180:
            return design_w - x - width, design_h - y - height, width, height
        if self.op == Image.Transpose.ROTATE_90:  # counter-clockwise: (x, y) -> (y, W - 1 - x)
            return y, design_w - x - width, height, width
        return design_h - y - height, x, height, width  # clockwise: (x, y) -> (H - 1 - y, x)


def compile_widgets(background, fill, widgets, orientation):
    """Theme file from a background (design size, RGB) and widgets in design coordinates:
      {"type": "number", "tag", "x", "y", "font": DigitFont, "colour": rgb, "share": key}    one digit
      {"type": "picture", "tag", "x", "y", "width", "height", "colour": rgb, "glyphs": {"0": mask, ...}, "share": key}
    Widgets with the same share key point to one copy of their glyphs (all lamps of a size, the digits of a font)."""
    if background.size != orientation.size:
        raise ThemeError(f"achtergrond van {background.size}, verwacht {orientation.size}")
    records, resources, shares = [], [], {}
    offset = TABLE_SIZE + BACKGROUND_SIZE
    design_w, design_h = orientation.size
    for widget in widgets:
        tag, x, y = widget["tag"], widget["x"], widget["y"]
        if not 1 <= tag <= 255 or tag == CLOCK_TAG:
            raise ThemeError(f"tag {tag}: 1..255 en niet {CLOCK_TAG} (de klok)")
        if widget["type"] == "number":
            font = widget["font"]
            width, height = font.digit_width, font.height
            cells = {c: font.cell(c) for c in NUMBER_GLYPHS}
        elif widget["type"] == "picture":
            width, height, glyphs = widget["width"], widget["height"], widget["glyphs"]
            if not any(c in glyphs for c in "0123456789"):
                raise ThemeError(f"afbeelding {tag}: geen enkel plaatje")
            for character, mask in glyphs.items():
                if mask.size != (width, height):
                    raise ThemeError(f"afbeelding {tag}: plaatje {character} moet {width}x{height} zijn")
            cells = {c: glyphs.get(c) for c in NUMBER_GLYPHS}
        else:
            raise ThemeError(f"onbekend widget-type {widget['type']}")
        if x < 0 or y < 0 or x + width > design_w or y + height > design_h:
            raise ThemeError(f"widget {tag}: vak {x},{y} van {width}x{height} valt buiten het scherm")
        px, py, pw, ph = orientation.box(x, y, width, height)
        share = widget.get("share")
        known = shares.get(share) if share else None
        parts = []
        if known:
            if known["size"] != (pw, ph):
                raise ThemeError(f"gedeelde plaatjes {share} met een andere maat")
            at, widths = known["offset"], known["widths"]
        else:
            at, widths = offset, []
            for character in NUMBER_GLYPHS:
                cell = cells[character]
                if cell is None:  # a picture without this glyph: 1 pixel wide and empty
                    parts.append(bytes(ph))
                    widths.append(1)
                else:
                    parts.append(orientation.image(cell).tobytes())
                    widths.append(pw)
            if share:
                shares[share] = {"offset": offset, "widths": widths, "size": (pw, ph)}
        record = head(TYPE_NUMBER, len(records) + 1, tag, px, py, pw, ph)
        record += struct.pack(">BHBBIH", 0, rgb565(widget["colour"]), 0, 0xFF, at, ph)
        record += struct.pack(">12H", *widths)
        records.append(slot(record))
        for part in parts:
            resources.append(part)
            offset += len(part)
        if len(records) > MAX_WIDGET_RECORDS:
            raise ThemeError(f"meer dan {MAX_WIDGET_RECORDS} widgets")
    if offset > MAX_THEME_BYTES:
        raise ThemeError(f"thema is {offset} bytes, het scherm toont tot {MAX_THEME_BYTES}")
    background_record = head(TYPE_BACKGROUND, 0, 0, 0, 0, SCREEN_WIDTH, SCREEN_HEIGHT)
    background_record += struct.pack(">BHIB", 0, rgb565(fill), TABLE_SIZE, 1)
    table = (slot(b"\x96") + slot(background_record) + b"".join(records)).ljust(TABLE_SIZE, b"\x00")
    return table + rgb_plane(orientation.image(background)) + b"".join(resources)


def _lamps(widgets, x, y, diameter, gap, tags, masks):
    """Three lamps side by side: green, orange, red (each 0 = dimmed, 1 = on), sharing their pictures."""
    for i, colour in enumerate(("green", "orange", "red")):
        widgets.append({"type": "picture", "tag": tags[i], "x": x + i * (diameter + gap), "y": y, "width": diameter,
                        "height": diameter, "colour": COLOURS[colour], "glyphs": masks, "share": f"lamp{diameter}"})


def _lamp_set(diameter):
    return {"0": lamp_mask(diameter, DIM), "1": lamp_mask(diameter, 1)}


def _digits(widgets, tags, x, y, font, colour, share):
    for i, tag in enumerate(tags):
        widgets.append({"type": "number", "tag": tag, "x": x + i * font.digit_width, "y": y, "font": font,
                        "colour": colour, "share": share})


def _clock(painter, widgets, right, centre_y, colour):
    """HH:mm as four digit widgets + a ':' in the background (the screen's own clock widget cannot be turned)."""
    font = DigitFont(FONT_BOLD, 26)
    colon = font.advance(":")
    left, top = right - (4 * font.digit_width + colon), rnd(centre_y - font.height / 2)
    _digits(widgets, TAGS["clock"][:2], left, top, font, colour, "clock")
    painter.glyph(font.mask(":", colon), left + 2 * font.digit_width, top, colour)
    _digits(widgets, TAGS["clock"][2:], left + 2 * font.digit_width + colon, top, font, colour, "clock")
    return left


def _name_glyphs(names, width, height):
    shown = (names or ["…"])[:MAX_NAMES]
    return {str(i): text_mask(width, height, n, FONT_BOLD, min(32, height - 6), 14, lines=2)
            for i, n in enumerate(shown)}


def _layout_landscape(names, labels, name_h):
    """480 x 320, as the browser page draws it."""
    width, height = SCREEN_WIDTH, SCREEN_HEIGHT
    c = COLOURS
    painter, widgets = Painter((width, height), c["cream"]), []

    # Header: "Arcoloo ops" left, on the right the counter (2/5) and the clock (HH:mm).
    header = 46
    painter.rect(0, 0, width, header, c["navy"])
    clock_left = _clock(painter, widgets, width - 14, header / 2, c["white"])
    painter.text("Arcoloo ops", 14, header / 2 + 1, FONT_BOLD, 18, c["white"])
    counter, slash = DigitFont(FONT_BOLD, 18), 10
    x0, y0 = clock_left - 22 - (4 * counter.digit_width + slash), rnd(header / 2 - counter.height / 2)
    _digits(widgets, TAGS["pos"], x0, y0, counter, c["soft"], "pos")
    painter.text("/", x0 + 2 * counter.digit_width + slash / 2, header / 2 + 1, FONT_BOLD, 18, c["soft"], "center")
    _digits(widgets, TAGS["total"], x0 + 2 * counter.digit_width + slash, y0, counter, c["soft"], "pos")

    # Big traffic light + the name of the shop (picture i = name i).
    big, gap, bx, by = 34, 8, 14, header + 10
    bw, bh = 10 + 3 * big + 2 * gap + 10, big + 20
    painter.round_rect(bx, by, bw, bh, 14, c["housing"])
    _lamps(widgets, bx + 10, by + 10, big, gap, [TAGS["lamp_ok"], TAGS["lamp_warn"], TAGS["lamp_bad"]],
           _lamp_set(big))
    ox = bx + bw + 14
    ow, oy = width - 12 - ox, by + rnd((bh - name_h) / 2)
    widgets.append({"type": "picture", "tag": TAGS["name"], "x": ox, "y": oy, "width": ow, "height": name_h,
                    "colour": c["navy"], "glyphs": _name_glyphs(names, ow, name_h)})

    # Eight tiles (2 columns x 4 rows), each with a small traffic light.
    top, foot, rows, gap_y, gap_x, mx = by + bh + 10, 288, 4, 5, 8, 10
    th, tw = (foot - 6 - top - (rows - 1) * gap_y) // rows, (width - 2 * mx - gap_x) // 2
    _tiles(painter, widgets, labels, mx, top, tw, th, gap_x, gap_y, 2, 17)

    # Footer: [text, right aligned] [3 digits] [min geleden].
    painter.rect(0, foot, width, height - foot, c["navy"])
    fh, lw, lx = 22, 238, 14
    fy = foot + rnd((height - foot - fh) / 2)
    widgets.append({"type": "picture", "tag": TAGS["footer"], "x": lx, "y": fy, "width": lw, "height": fh,
                    "colour": c["soft"], "glyphs": {str(i): text_mask(lw, fh, t, FONT_MEDIUM, 15, 11, align="right")
                                                    for i, t in enumerate(FOOTER_TEXT)}})
    number = DigitFont(FONT_BOLD, 19)
    nx, ny = lx + lw + 8, rnd(foot + (height - foot - number.height) / 2)
    _digits(widgets, TAGS["minutes"], nx, ny, number, c["white"], "min")
    ux, uw = nx + 3 * number.digit_width + 6, 110
    widgets.append({"type": "picture", "tag": TAGS["unit"], "x": ux, "y": fy, "width": uw, "height": fh,
                    "colour": c["soft"], "glyphs": {"0": text_mask(uw, fh, "min geleden", FONT_MEDIUM, 15, 11)}})
    return painter.finish(), widgets


def _layout_portrait(names, labels, name_h):
    """320 x 480: header, traffic light + counter, the name over the full width, the tiles under each other, a footer
    of two lines."""
    width, height = SCREEN_HEIGHT, SCREEN_WIDTH
    c = COLOURS
    painter, widgets = Painter((width, height), c["cream"]), []

    header = 46
    painter.rect(0, 0, width, header, c["navy"])
    _clock(painter, widgets, width - 14, header / 2, c["white"])
    painter.text("Arcoloo ops", 14, header / 2 + 1, FONT_BOLD, 18, c["white"])

    big, gap, bx, by = 34, 8, 14, header + 10
    bw, bh = 10 + 3 * big + 2 * gap + 10, big + 20
    painter.round_rect(bx, by, bw, bh, 14, c["housing"])
    _lamps(widgets, bx + 10, by + 10, big, gap, [TAGS["lamp_ok"], TAGS["lamp_warn"], TAGS["lamp_bad"]],
           _lamp_set(big))
    counter, slash = DigitFont(FONT_BOLD, 22), 12
    x0 = width - 14 - (4 * counter.digit_width + slash)
    y0 = by + rnd((bh - counter.height) / 2)
    _digits(widgets, TAGS["pos"], x0, y0, counter, c["navy"], "pos")
    painter.text("/", x0 + 2 * counter.digit_width + slash / 2, by + bh / 2 + 1, FONT_BOLD, 22, c["navy"], "center")
    _digits(widgets, TAGS["total"], x0 + 2 * counter.digit_width + slash, y0, counter, c["navy"], "pos")

    ox, oy, ow = 14, by + bh + 8, width - 28
    widgets.append({"type": "picture", "tag": TAGS["name"], "x": ox, "y": oy, "width": ow, "height": name_h,
                    "colour": c["navy"], "glyphs": _name_glyphs(names, ow, name_h)})

    foot, rows, gap_y, mx = height - 54, len(labels), 4, 10
    top = oy + name_h + 8
    th, tw = (foot - 6 - top - (rows - 1) * gap_y) // rows, width - 2 * mx
    _tiles(painter, widgets, labels, mx, top, tw, th, 0, gap_y, 1, 16)

    # Footer: line 1 the text, line 2 [3 digits] min geleden.
    painter.rect(0, foot, width, height - foot, c["navy"])
    fh, lx = 20, 14
    widgets.append({"type": "picture", "tag": TAGS["footer"], "x": lx, "y": foot + 5, "width": width - 28,
                    "height": fh, "colour": c["soft"],
                    "glyphs": {str(i): text_mask(width - 28, fh, t, FONT_MEDIUM, 15, 11)
                               for i, t in enumerate(FOOTER_TEXT)}})
    number = DigitFont(FONT_BOLD, 19)
    ny = foot + 28 + rnd((22 - number.height) / 2)
    _digits(widgets, TAGS["minutes"], lx, ny, number, c["white"], "min")
    ux, uw = lx + 3 * number.digit_width + 6, 120
    widgets.append({"type": "picture", "tag": TAGS["unit"], "x": ux, "y": ny + rnd((number.height - fh) / 2),
                    "width": uw, "height": fh, "colour": c["soft"],
                    "glyphs": {"0": text_mask(uw, fh, "min geleden", FONT_MEDIUM, 15, 11)}})
    return painter.finish(), widgets


def _tiles(painter, widgets, labels, mx, top, tw, th, gap_x, gap_y, columns, label_px):
    c = COLOURS
    small, small_gap = 13, 4
    sw, sh = 6 + 3 * small + 2 * small_gap + 6, small + 8
    masks = _lamp_set(small)
    for i, label in enumerate(labels):
        column, row = i % columns, i // columns
        x, y = mx + column * (tw + gap_x), top + row * (th + gap_y)
        painter.round_rect(x + 0.5, y + 0.5, tw - 1, th - 1, 8 if th > 30 else 7, c["white"], c["line"])
        hx, hy = x + 6, y + rnd((th - sh) / 2)
        painter.round_rect(hx, hy, sw, sh, sh / 2, c["housing"])
        base = TAGS["tile_base"] + 3 * i
        _lamps(widgets, hx + 6, hy + 4, small, small_gap, [base, base + 1, base + 2], masks)
        painter.text(label, hx + sw + 10, y + th / 2 + 1, FONT_MEDIUM, label_px, c["navy"])


NAME_HEIGHTS = {"landscape": (44, 38, 32, 28), "portrait": (52, 44, 38, 32)}


def build_status_theme(names, labels=None, orientation="landscape", upside_down=False):
    """The theme file for these names. Does it not fit (many long names), smaller name pictures."""
    turn = Orientation(orientation, upside_down)
    labels = list(labels or TILE_LABELS)[:len(TILE_LABELS)]
    layout = _layout_portrait if turn.portrait else _layout_landscape
    for name_h in NAME_HEIGHTS[orientation]:
        try:
            background, widgets = layout(names, labels, name_h)
            return compile_widgets(background, COLOURS["cream"], widgets, turn)
        except ThemeError:
            continue
    background, widgets = layout(list(names)[:4], labels, NAME_HEIGHTS[orientation][-1])
    return compile_widgets(background, COLOURS["cream"], widgets, turn)


# ---------------------------------------------------------------- settings and the connection with ops

def normalize_url(text):
    """The address of ops: https://..., or http:// for this PC only (tests). Only the host counts (ops is at the
    root); a pasted .../minischerm.html is fine."""
    parts = urllib.parse.urlsplit(str(text or "").strip())
    if not parts.hostname:
        raise ValueError("geef het adres van ops, bv. https://arcoloomonitoring.up.railway.app")
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")):
        return f"{parts.scheme}://{parts.netloc}"
    raise ValueError("het adres van ops moet met https:// beginnen")


def check_key(key):
    key = str(key or "").strip()
    if not KEY_RE.match(key):
        raise ValueError("de schermsleutel begint met msk_ en heeft 47 tekens (ops: Monitor -> Minischerm)")
    return key


def masked(key):
    return (key[:8] + "…") if key else "-"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the key goes to the address of ops and nowhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch_status(base_url, key, timeout=10):
    """(HTTP status, payload or None). Status 0 = no connection."""
    request = urllib.request.Request(base_url + STATUS_PATH, headers={
        "x-display-key": key, "Accept": "application/json", "Cache-Control": "no-cache",
        "User-Agent": "smartmonitor-ops"})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read(4_000_000).decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


class Poller:
    """Fetches the status every 30 s in the background; snapshot() gives the latest state (times in ms)."""

    def __init__(self, base_url, key, interval=POLL_S, fetch=fetch_status, clock=time.time):
        self.base_url, self.key, self.interval, self.fetch, self.clock = base_url, key, interval, fetch, clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._state = {"payload": None, "fetched_at": 0, "last_ok_at": self.clock() * 1000, "error": None}
        self.last_status = None

    def poll_once(self):
        status, payload = self.fetch(self.base_url, self.key)
        now = self.clock() * 1000
        with self._lock:
            previous = self._state
            if status == 200 and isinstance(payload, dict):
                self._state = {"payload": payload, "fetched_at": now, "last_ok_at": now, "error": None}
            elif status == 401:
                self._state = {"payload": None, "fetched_at": 0, "last_ok_at": previous["last_ok_at"],
                               "error": "denied"}
            else:
                self._state = dict(previous, error="offline")
            self.last_status = status
        return status

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - the screen keeps running; the next poll tries again
                pass

    def start(self):
        threading.Thread(target=self._run, name="ops-poller", daemon=True).start()

    def stop(self):
        self._stop.set()


class OpsScreen:
    """Drives the screen: puts the theme on it when the names, labels or orientation change, then every second the
    numbers (when they change, and every 10 s anyway) and the clock.

    store: get_digest() / set_digest(digest, names, labels) / last_names() / last_labels(), the theme on the screen.
    say(text): a line for the log."""

    def __init__(self, transport, poller, store, seconds=DEFAULT_SECONDS, orientation="landscape", upside_down=False,
                 say=print, upload=protocol.upload_theme):
        self.transport, self.poller, self.store, self.say, self.upload = transport, poller, store, say, upload
        self.seconds = norm_seconds(seconds)
        self.orientation, self.upside_down = orientation, bool(upside_down)
        Orientation(orientation, upside_down)  # refuses an unknown orientation now, not at the first tick
        self.theme_key, self.theme = None, None
        self.last_pairs, self.ticks = None, 0

    def wanted_theme(self, state):
        payload = state.get("payload")
        if isinstance(payload, dict):
            names, labels = name_list(targets_of(payload)), tile_labels(payload)
        elif self.theme_key is not None:
            names, labels = list(self.theme_key[0]), list(self.theme_key[1])
        else:
            names, labels = self.store.last_names() or [], self.store.last_labels() or list(TILE_LABELS)
        key = (tuple(names), tuple(labels), self.orientation, self.upside_down)
        if key != self.theme_key:
            self.theme = build_status_theme(names, labels, self.orientation, self.upside_down)
            self.theme_key = key
        return self.theme

    def ensure_theme(self, state, force=False):
        data = self.wanted_theme(state)
        digest = hashlib.sha256(data).hexdigest()
        if not force and self.store.get_digest() == digest:
            return False
        names = list(self.theme_key[0])
        self.say(f"Thema opladen ({len(data)} bytes, {len(names)} naam/namen, {self.orientation}"
                 f"{', omgedraaid' if self.upside_down else ''}); het schermpje start twee keer opnieuw op...")
        started = time.monotonic()
        self.upload(self.transport, data, None)
        self.store.set_digest(digest, names, list(self.theme_key[1]))
        self.say(f"Thema opgeladen in {time.monotonic() - started:.0f} s.")
        self.last_pairs = None
        return True

    def tick(self, moment, now_ms=None):
        state = self.poller.snapshot()
        self.ensure_theme(state)
        now_ms = time.time() * 1000 if now_ms is None else now_ms
        pairs = tag_pairs(view_for(state, now_ms, self.seconds), moment)
        reports = []
        if pairs != self.last_pairs or self.ticks % 10 == 0:
            reports += protocol.value_reports(pairs)
        reports.append(protocol.clock_report(moment))
        for report in reports:
            self.transport.write(report)
        self.last_pairs = pairs
        self.ticks += 1
        return pairs
