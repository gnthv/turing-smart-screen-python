# SPDX-License-Identifier: GPL-3.0-or-later
#
# Theme compiler for the SmartMonitor screen: a theme folder (theme.yaml + optional PNG/TTF files) becomes the
# theme file img.dat that the screen plays, and a compiled theme can be drawn back as a preview PNG.
#
# Layout of img.dat (multi-byte integers of the record table big-endian, pixels little-endian):
#   0x0000-0x0FFF  record table: 64 slots of 64 bytes. Slot 0 = header (byte 0 = 0x96), slot 1 = background,
#                  slot 2.. = widgets
#   0x1000-        background: 480 x 320 RGB565 pixels, then the resources of the widgets back to back
# The screen draws no widget whose data ends past 503 808 bytes, so a larger theme is refused.

import math
import os
import re
import struct
import datetime

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont

SCREEN_WIDTH = 480
SCREEN_HEIGHT = 320
SLOT_SIZE = 64
TABLE_SIZE = 64 * SLOT_SIZE
BACKGROUND_SIZE = SCREEN_WIDTH * SCREEN_HEIGHT * 2
MAX_THEME_BYTES = 503_808
MAX_WIDGET_RECORDS = 62
CLOCK_TAG = 21

TYPE_BACKGROUND = 0x81
TYPE_IMAGE = 0x84
TYPE_BAR = 0x8B
TYPE_DATETIME = 0x8E
TYPE_NUMBER = 0x92
TYPE_TEXT = 0x93

NUMBER_GLYPHS = "0123456789.-"
DATETIME_OTHERS = ".-/ :"  # followed in the file by 6 glyphs (CJK units) that a format never uses
DATETIME_TOKENS = [("YYYY", "1"), ("MM", "2"), ("DD", "3"), ("HH", "4"), ("mm", "5"), ("ss", "6")]
BAR_SAMPLE_BYTES = 0x76B0  # bytes 18-19 and 26-27 of every bar of the vendor sample; changing them showed nothing

METRIC_NAME = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)*$")
ALIGNMENTS = {"left": 0, "center": 1, "right": 2}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ThemeError(Exception):
    pass


# ---------------------------------------------------------------- colours and pixels

def parse_colour(value, place):
    text = str(value).strip()
    match = re.fullmatch(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})", text)
    if not match:
        raise ThemeError(f"{place}: kleur '{value}' is geen #rrggbb")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def rgb565(rgb):
    red, green, blue = rgb
    return ((red * 31 + 127) // 255) << 11 | ((green * 63 + 127) // 255) << 5 | ((blue * 31 + 127) // 255)


def rgb565_to_rgb(colour):
    red, green, blue = (colour >> 11) & 31, (colour >> 5) & 63, colour & 31
    return (red << 3 | red >> 2, green << 2 | green >> 4, blue << 3 | blue >> 2)


def rgb_plane(picture: Image.Image) -> bytes:
    """RGB565 pixels, little-endian, row by row."""
    array = np.asarray(picture.convert("RGB"), dtype=np.uint32)
    red = (array[..., 0] * 31 + 127) // 255
    green = (array[..., 1] * 63 + 127) // 255
    blue = (array[..., 2] * 31 + 127) // 255
    return ((red << 11) | (green << 5) | blue).astype("<u2").tobytes()


def plane_to_image(data: bytes, width: int, height: int) -> Image.Image:
    pixels = np.frombuffer(data, dtype="<u2", count=width * height).reshape(height, width).astype(np.uint32)
    red, green, blue = (pixels >> 11) & 31, (pixels >> 5) & 63, pixels & 31
    rgb = np.stack([red << 3 | red >> 2, green << 2 | green >> 4, blue << 3 | blue >> 2], axis=-1).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


# ---------------------------------------------------------------- fonts and glyphs

class GlyphFont:
    """A TTF font drawn at a size in pixels, as the screen's pre-rendered alpha masks."""

    def __init__(self, path, size, colour, name):
        self.name = name
        self.colour = colour
        self.path = path
        self.size = size
        self.font = ImageFont.truetype(path, size)
        ascent, descent = self.font.getmetrics()
        self.ascent = ascent
        self.height = ascent + descent
        self.digit_width = max(self.advance(d) for d in "0123456789")

    def advance(self, character):
        return max(1, math.ceil(self.font.getlength(character)))

    def _mask(self, text, width, x=0):
        mask = Image.new("L", (width, self.height), 0)
        ImageDraw.Draw(mask).text((x, 0), text, font=self.font, fill=255)
        return mask

    def digit(self, character):
        offset = (self.digit_width - self.advance(character)) // 2
        return self._mask(character, self.digit_width, offset).tobytes()

    def glyph(self, character):
        """(mask bytes, width) of a number glyph: digits in equal cells, '.' and '-' at their own advance."""
        if character.isdigit():
            return self.digit(character), self.digit_width
        width = self.advance(character)
        return self._mask(character, width).tobytes(), width

    def blank(self):
        return bytes(self.digit_width * self.height)

    def text_width(self, text):
        return max(1, math.ceil(self.font.getlength(text)))


# ---------------------------------------------------------------- records

def head(record_type, index, tag, x, y, width, height):
    return struct.pack(">BBBHHHH", record_type, index, tag, x, y, width, height)


def slot(data: bytes) -> bytes:
    if len(data) > SLOT_SIZE:
        raise ValueError("record longer than a slot")
    return data.ljust(SLOT_SIZE, b"\x00")


class CompiledTheme:
    def __init__(self, data, tags, metrics, name, costs):
        self.data = data          # bytes of img.dat
        self.tags = tags          # {metric name: tag}
        self.metrics = metrics    # custom metric definitions of the theme
        self.name = name
        self.costs = costs        # [(description, bytes)]


def _int(spec, key, place, default=None, minimum=0, maximum=None):
    value = spec.get(key, default)
    if value is None:
        raise ThemeError(f"{place}: '{key}' ontbreekt")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ThemeError(f"{place}: '{key}' moet een geheel getal zijn")
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" tot {maximum}" if maximum is not None else ""
        raise ThemeError(f"{place}: '{key}' moet van {minimum}{upper} zijn")
    return value


def _check_box(place, x, y, width, height):
    if x + width > SCREEN_WIDTH or y + height > SCREEN_HEIGHT:
        raise ThemeError(f"{place}: vak {x},{y} van {width}x{height} valt buiten het scherm van "
                         f"{SCREEN_WIDTH}x{SCREEN_HEIGHT}")


def _find_file(folder, relative, place):
    for base in (folder, REPO_ROOT):
        candidate = os.path.normpath(os.path.join(base, relative))
        if os.path.isfile(candidate):
            return candidate
    raise ThemeError(f"{place}: bestand '{relative}' niet gevonden (in de themamap of de projectmap)")


def compile_datetime_format(text, place):
    """'DD/MM/YYYY' -> '3/2/1'. Tokens: YYYY MM DD HH mm ss; other characters: . - / : space."""
    out, rest = "", str(text)
    while rest:
        for token, code in DATETIME_TOKENS:
            if rest.startswith(token):
                out += code
                rest = rest[len(token):]
                break
        else:
            character = rest[0]
            if character not in DATETIME_OTHERS:
                raise ThemeError(f"{place}: '{character}' kan niet in een datum/tijd; gebruik YYYY MM DD HH mm ss "
                                 f"en . - / : of spatie")
            out += character
            rest = rest[1:]
    if not out or len(out) > 19:
        raise ThemeError(f"{place}: formaat is leeg of te lang")
    return out


def load_theme_yaml(folder):
    path = os.path.join(folder, "theme.yaml")
    if not os.path.isfile(path):
        raise ThemeError(f"geen theme.yaml in {folder}")
    with open(path, encoding="utf-8") as stream:
        try:
            spec = yaml.safe_load(stream) or {}
        except yaml.YAMLError as error:
            raise ThemeError(f"theme.yaml is geen geldige YAML: {error}") from error
    if not isinstance(spec, dict):
        raise ThemeError("theme.yaml: verwacht sleutels zoals background, fonts en widgets")
    return spec


def _custom_metrics(spec):
    metrics = spec.get("metrics") or {}
    if not isinstance(metrics, dict):
        raise ThemeError("theme.yaml: 'metrics' moet een lijst van naam: {command: ...} zijn")
    for name, definition in metrics.items():
        place = f"theme.yaml: metric {name}"
        if not METRIC_NAME.match(str(name)):
            raise ThemeError(f"{place}: naam met kleine letters, cijfers, _ - en punten (bv. weer.temp)")
        if not isinstance(definition, dict) or not str(definition.get("command", "")).strip():
            raise ThemeError(f"{place}: 'command' ontbreekt")
        every = definition.get("every", 10)
        if isinstance(every, bool) or not isinstance(every, (int, float)) or every < 1:
            raise ThemeError(f"{place}: 'every' moet minstens 1 (seconden) zijn")
        scale = definition.get("scale", 1)
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise ThemeError(f"{place}: 'scale' moet een getal zijn")
    return metrics


def compile_theme(folder) -> CompiledTheme:
    folder = os.path.abspath(folder)
    spec = load_theme_yaml(folder)
    name = str(spec.get("name") or os.path.basename(folder))
    custom = _custom_metrics(spec)

    fonts = {}
    for font_name, font_spec in (spec.get("fonts") or {}).items():
        place = f"theme.yaml: font {font_name}"
        if not isinstance(font_spec, dict) or "file" not in font_spec:
            raise ThemeError(f"{place}: 'file' ontbreekt")
        path = _find_file(folder, font_spec["file"], place)
        size = _int(font_spec, "size", place, default=20, minimum=6, maximum=200)
        colour = parse_colour(font_spec.get("color", "#ffffff"), place)
        try:
            fonts[font_name] = GlyphFont(path, size, colour, font_name)
        except OSError as error:
            raise ThemeError(f"{place}: '{font_spec['file']}' is geen bruikbaar lettertype ({error})") from error

    # Background: a colour or a PNG (scaled to 480x320), composited over black.
    background_spec = spec.get("background", "#000000")
    fill_colour = 0
    if isinstance(background_spec, str) and re.fullmatch(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})", background_spec.strip()):
        rgb = parse_colour(background_spec, "theme.yaml: background")
        picture = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), rgb)
        fill_colour = rgb565(rgb)
    else:
        path = _find_file(folder, str(background_spec), "theme.yaml: background")
        try:
            loaded = Image.open(path).convert("RGBA")
        except OSError as error:
            raise ThemeError(f"theme.yaml: background '{background_spec}' is geen afbeelding") from error
        if loaded.size != (SCREEN_WIDTH, SCREEN_HEIGHT):
            loaded = loaded.resize((SCREEN_WIDTH, SCREEN_HEIGHT), Image.LANCZOS)
        picture = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (0, 0, 0))
        picture.paste(loaded, (0, 0), loaded)
    draw = ImageDraw.Draw(picture)

    widgets = spec.get("widgets") or []
    if not isinstance(widgets, list):
        raise ThemeError("theme.yaml: 'widgets' moet een lijst zijn")

    # Tags: every metric used by a number or a bar gets one, in the order of the metric names, skipping 21 (clock).
    used = set()
    for number, widget in enumerate(widgets, start=1):
        if isinstance(widget, dict) and widget.get("type") in ("number", "bar"):
            metric = str(widget.get("metric", ""))
            if not METRIC_NAME.match(metric):
                raise ThemeError(f"theme.yaml: widget {number} ({widget.get('type')}): 'metric' ontbreekt of is "
                                 f"ongeldig (bv. cpu.percent)")
            used.add(metric)
    tags, next_tag = {}, 1
    for metric in sorted(used):
        if next_tag == CLOCK_TAG:
            next_tag += 1
        if next_tag > 255:
            raise ThemeError("theme.yaml: te veel verschillende metrics")
        tags[metric] = next_tag
        next_tag += 1

    records, resources, costs = [], [], []
    offset = TABLE_SIZE + BACKGROUND_SIZE

    def add(record_bytes, resource, description):
        nonlocal offset
        records.append(record_bytes)
        resources.append(resource)
        offset += len(resource)
        if resource:
            costs.append((description, len(resource)))

    for number, widget in enumerate(widgets, start=1):
        if not isinstance(widget, dict):
            raise ThemeError(f"theme.yaml: widget {number} is geen sleutel/waarde-lijst")
        kind = widget.get("type")
        place = f"theme.yaml: widget {number} ({kind})"
        index = len(records) + 1  # slot - 1

        def font_of():
            font_name = widget.get("font")
            if font_name not in fonts:
                raise ThemeError(f"{place}: lettertype '{font_name}' staat niet onder fonts")
            return fonts[font_name]

        if kind == "rect":
            x, y = _int(widget, "x", place), _int(widget, "y", place)
            width, height = _int(widget, "width", place, minimum=1), _int(widget, "height", place, minimum=1)
            _check_box(place, x, y, width, height)
            colour = parse_colour(widget.get("color", "#202020"), place)
            radius = _int(widget, "radius", place, default=0)
            outline = parse_colour(widget["outline"], place) if widget.get("outline") else None
            draw.rounded_rectangle([x, y, x + width - 1, y + height - 1], radius=radius, fill=colour, outline=outline)

        elif kind == "text":
            font = font_of()
            text = str(widget.get("text", ""))
            if not text:
                raise ThemeError(f"{place}: 'text' ontbreekt")
            width = font.text_width(text)
            align = widget.get("align", "left")
            if align not in ALIGNMENTS:
                raise ThemeError(f"{place}: align is left, center of right")
            x, y = _int(widget, "x", place), _int(widget, "y", place)
            left = x - (width // 2 if align == "center" else width if align == "right" else 0)
            colour = parse_colour(widget["color"], place) if widget.get("color") else font.colour
            if left < 0:
                raise ThemeError(f"{place}: tekst begint links buiten het scherm")
            _check_box(place, left, y, width, font.height)
            draw.text((left, y), text, font=font.font, fill=colour)

        elif kind == "image":
            path = _find_file(folder, str(widget.get("file", "")), place)
            try:
                image = Image.open(path).convert("RGBA")
            except OSError as error:
                raise ThemeError(f"{place}: '{widget.get('file')}' is geen afbeelding") from error
            if widget.get("width") or widget.get("height"):
                image = image.resize((_int(widget, "width", place, default=image.width, minimum=1),
                                      _int(widget, "height", place, default=image.height, minimum=1)), Image.LANCZOS)
            x, y = _int(widget, "x", place), _int(widget, "y", place)
            _check_box(place, x, y, image.width, image.height)
            resource = image.getchannel("A").tobytes() + rgb_plane(image)
            record = head(TYPE_IMAGE, index, 0, x, y, image.width, image.height)
            record += struct.pack(">IBBH", offset, 1, 1, 0)
            add(slot(record), resource, f"afbeelding {widget.get('file')}")

        elif kind == "number":
            font = font_of()
            digits = _int(widget, "digits", place, default=3, minimum=1, maximum=12)
            align = widget.get("align", "right")
            if align not in ALIGNMENTS:
                raise ThemeError(f"{place}: align is left, center of right")
            x, y = _int(widget, "x", place), _int(widget, "y", place)
            width = digits * font.digit_width
            _check_box(place, x, y, width, font.height)
            colour = parse_colour(widget["color"], place) if widget.get("color") else font.colour
            widths, resource = [], b""
            for character in NUMBER_GLYPHS:
                mask, glyph_width = font.glyph(character)
                widths.append(glyph_width)
                resource += mask
            record = head(TYPE_NUMBER, index, tags[widget["metric"]], x, y, width, font.height)
            record += struct.pack(">BHBBIH", ALIGNMENTS[align], rgb565(colour), 1 if widget.get("thousands") else 0,
                                  0xFF, offset, font.height)
            record += struct.pack(">12H", *widths)
            add(slot(record), resource, f"getal {widget['metric']} ({font.name})")

        elif kind == "bar":
            x, y = _int(widget, "x", place), _int(widget, "y", place)
            width = _int(widget, "width", place, minimum=3)
            height = _int(widget, "height", place, minimum=3)
            _check_box(place, x, y, width, height)
            colour = rgb565(parse_colour(widget.get("color", "#00c8ff"), place))
            empty = rgb565(parse_colour(widget.get("background", "#000000"), place))
            record = head(TYPE_BAR, index, tags[widget["metric"]], x, y, width, height)
            record += struct.pack(">BHHHH", 1, empty, colour, colour, BAR_SAMPLE_BYTES)
            record += bytes(6) + struct.pack(">H", BAR_SAMPLE_BYTES)
            add(slot(record), b"", "balk")

        elif kind == "datetime":
            font = font_of()
            code = compile_datetime_format(widget.get("format", "HH:mm:ss"), place)
            width = 0
            for character in code:
                width += 4 * font.digit_width if character == "1" else \
                    2 * font.digit_width if character in "23456" else font.advance(character)
            x, y = _int(widget, "x", place), _int(widget, "y", place)
            _check_box(place, x, y, width, font.height)
            colour = parse_colour(widget["color"], place) if widget.get("color") else font.colour
            resource = b"".join(font.digit(d) for d in "0123456789")
            others = []
            for character in DATETIME_OTHERS:
                mask, glyph_width = font._mask(character, font.advance(character)).tobytes(), font.advance(character)
                others.append(glyph_width)
                resource += mask
            for _ in range(6):
                others.append(font.digit_width)
                resource += font.blank()
            record = head(TYPE_DATETIME, index, CLOCK_TAG, x, y, width, font.height)
            record += struct.pack(">BHBIHH", 1, rgb565(colour), 0xFF, offset, font.height, font.digit_width)
            record += struct.pack(">11H", *others)
            record += code.encode("ascii")
            add(slot(record), resource, f"datum/tijd {widget.get('format', 'HH:mm:ss')}")

        else:
            raise ThemeError(f"{place}: onbekend type; kies rect, text, image, number, bar of datetime")

        if len(records) > MAX_WIDGET_RECORDS:
            raise ThemeError(f"theme.yaml: meer dan {MAX_WIDGET_RECORDS} widgets (tekst en rect tellen niet mee)")

    background_record = head(TYPE_BACKGROUND, 0, 0, 0, 0, SCREEN_WIDTH, SCREEN_HEIGHT)
    background_record += struct.pack(">BHIB", 0, fill_colour, TABLE_SIZE, 1)
    table = slot(b"\x96") + slot(background_record) + b"".join(records)
    table = table.ljust(TABLE_SIZE, b"\x00")
    data = table + rgb_plane(picture) + b"".join(resources)

    if len(data) > MAX_THEME_BYTES:
        biggest = sorted(costs, key=lambda c: -c[1])[:5]
        listing = ", ".join(f"{d}: {n} bytes" for d, n in biggest)
        raise ThemeError(f"thema is {len(data)} bytes, {len(data) - MAX_THEME_BYTES} te veel (het scherm toont tot "
                         f"{MAX_THEME_BYTES}). Grootste delen: {listing}. Maak lettertypes of afbeeldingen kleiner.")
    return CompiledTheme(data, tags, custom, name, costs)


# ---------------------------------------------------------------- reading a compiled theme back (preview, tests)

def read_records(data: bytes):
    """The records of a theme file as dictionaries, in slot order."""
    if len(data) < TABLE_SIZE:
        raise ThemeError("geen themabestand: korter dan de recordtabel")
    records = []
    for number in range(1, 64):
        raw = data[number * SLOT_SIZE:(number + 1) * SLOT_SIZE]
        if not any(raw):
            continue
        kind, index, tag, x, y, width, height = struct.unpack(">BBBHHHH", raw[:11])
        record = {"slot": number, "type": kind, "index": index, "tag": tag, "x": x, "y": y,
                  "width": width, "height": height, "raw": raw}
        if kind == TYPE_BACKGROUND:
            record["fill"], record["offset"] = struct.unpack(">HI", raw[12:18])
        elif kind == TYPE_IMAGE:
            record["offset"], record["frames"] = struct.unpack(">IB", raw[11:16])
        elif kind == TYPE_NUMBER:
            align, colour, thousands, opacity, offset, glyph_height = struct.unpack(">BHBBIH", raw[11:22])
            record.update(align=align, colour=colour, thousands=thousands, opacity=opacity, offset=offset,
                          glyph_height=glyph_height, widths=list(struct.unpack(">12H", raw[22:46])))
        elif kind == TYPE_BAR:
            record["empty"], record["fill"], record["frame"] = struct.unpack(">HHH", raw[12:18])
        elif kind == TYPE_DATETIME:
            _, colour, _, offset, glyph_height, digit_width = struct.unpack(">BHBIHH", raw[11:23])
            record.update(colour=colour, offset=offset, glyph_height=glyph_height, digit_width=digit_width,
                          others=list(struct.unpack(">11H", raw[23:45])),
                          format=raw[45:].rstrip(b"\x00").decode("ascii", "replace"))
        elif kind == TYPE_TEXT:
            record["offset"], record["colour"] = struct.unpack(">IH", raw[11:17])
        records.append(record)
    return records


def number_text(value, thousands):
    value = int(value)
    if thousands:
        if abs(value) < 10000:
            whole = abs(value) // 100
            text = f"{whole // 10}.{whole % 10}"
        else:
            text = str(abs(value) // 1000)
        return ("-" if value < 0 else "") + text
    return str(value)


def _paint_mask(canvas, mask_bytes, width, height, x, y, colour, clip):
    if width <= 0 or height <= 0:
        return
    mask = Image.frombytes("L", (width, height), mask_bytes)
    left, top, right, bottom = clip
    box = (max(x, left), max(y, top), min(x + width, right), min(y + height, bottom))
    if box[0] >= box[2] or box[1] >= box[3]:
        return
    part = mask.crop((box[0] - x, box[1] - y, box[2] - x, box[3] - y))
    canvas.paste(Image.new("RGB", part.size, colour), (box[0], box[1]), part)


def render_preview(data: bytes, values=None, moment=None) -> Image.Image:
    """Draw a theme file the way the screen shows it, with values {tag: value}. A number without a value shows
    nothing, a bar without a value shows full (as on the device)."""
    values = values or {}
    moment = moment or datetime.datetime.now()
    canvas = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (0, 0, 0))
    for record in read_records(data):
        kind, x, y, width, height = record["type"], record["x"], record["y"], record["width"], record["height"]
        if kind == TYPE_BACKGROUND:
            canvas.paste(plane_to_image(data[record["offset"]:record["offset"] + width * height * 2], width, height),
                         (x, y))
        elif kind == TYPE_IMAGE:
            start = record["offset"]
            alpha = Image.frombytes("L", (width, height), data[start:start + width * height])
            colour = plane_to_image(data[start + width * height:start + 3 * width * height], width, height)
            canvas.paste(colour, (x, y), alpha)
        elif kind == TYPE_NUMBER:
            if record["tag"] not in values:
                continue
            text = number_text(values[record["tag"]], record["thousands"])
            widths, glyph_height = record["widths"], record["glyph_height"]
            starts, position = [], record["offset"]
            for glyph_width in widths:
                starts.append(position)
                position += glyph_width * glyph_height
            total = sum(widths[NUMBER_GLYPHS.index(c)] for c in text)
            left = x + {0: 0, 1: (width - total) // 2, 2: width - total}.get(record["align"], 0)
            colour = rgb565_to_rgb(record["colour"])
            for character in text:
                position = NUMBER_GLYPHS.index(character)
                glyph_width = widths[position]
                _paint_mask(canvas, data[starts[position]:starts[position] + glyph_width * glyph_height],
                            glyph_width, glyph_height, left, y, colour, (x, y, x + width, y + height))
                left += glyph_width
        elif kind == TYPE_BAR:
            draw = ImageDraw.Draw(canvas)
            value = values.get(record["tag"], 100)
            draw.rectangle([x, y, x + width - 1, y + height - 1], outline=rgb565_to_rgb(record["frame"]))
            inner = width - 2
            filled = inner * value // 100 if 0 <= value <= 100 else 0
            if height > 2 and inner > 0:
                draw.rectangle([x + 1, y + 1, x + width - 2, y + height - 2], fill=rgb565_to_rgb(record["empty"]))
                if filled > 0:
                    draw.rectangle([x + 1, y + 1, x + filled, y + height - 2], fill=rgb565_to_rgb(record["fill"]))
        elif kind == TYPE_DATETIME:
            glyph_height, digit_width = record["glyph_height"], record["digit_width"]
            fields = {"1": f"{moment.year:04d}", "2": f"{moment.month:02d}", "3": f"{moment.day:02d}",
                      "4": f"{moment.hour:02d}", "5": f"{moment.minute:02d}", "6": f"{moment.second:02d}"}
            others_start = record["offset"] + 10 * digit_width * glyph_height
            other_starts, position = {}, others_start
            for character, glyph_width in zip(DATETIME_OTHERS, record["others"]):
                other_starts[character] = (position, glyph_width)
                position += glyph_width * glyph_height
            left, colour = x, rgb565_to_rgb(record["colour"])
            clip = (0, 0, SCREEN_WIDTH, SCREEN_HEIGHT)
            for code in record["format"]:
                for character in fields.get(code, code):
                    if character.isdigit():
                        start = record["offset"] + int(character) * digit_width * glyph_height
                        glyph_width = digit_width
                    elif character in other_starts:
                        start, glyph_width = other_starts[character]
                    else:
                        continue
                    _paint_mask(canvas, data[start:start + glyph_width * glyph_height], glyph_width, glyph_height,
                                left, y, colour, clip)
                    left += glyph_width
    return canvas
