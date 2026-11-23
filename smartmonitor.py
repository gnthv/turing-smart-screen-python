#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
#
# smartmonitor.py: own dashboards on the SmartMonitor 3.5" HID screen (USB 0483:0065, "llhmi.com").
# This screen is not supported by main.py (it is no framebuffer): it plays a theme stored in its flash and receives
# only numbers and the clock. See LOKAAL.md.
#
#   python smartmonitor.py                 run: upload the theme when it changed, then send the values every second
#   python smartmonitor.py preview         draw the theme as preview.png (no screen needed)
#   python smartmonitor.py upload          upload the theme again, even when unchanged
#   python smartmonitor.py compile         write img.dat
#   python smartmonitor.py metrics         list the metrics with their current values
#   python smartmonitor.py devices         list the screens that are plugged in
#   python smartmonitor.py send 1=42 5=60  send test values for tags

import argparse
import datetime
import hashlib
import json
import os
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from library.smartmonitor import protocol  # noqa: E402
from library.smartmonitor.metrics import BUILTIN, Metrics  # noqa: E402
from library.smartmonitor.theme import ThemeError, compile_theme, render_preview  # noqa: E402

CONFIG_FILE = os.path.join(HERE, "smartmonitor.yaml")
STATE_FILE = os.path.join(HERE, "smartmonitor-state.json")
DEFAULT_THEME = "res/smartmonitor/themes/standaard"
SAMPLE_VALUE = 88


def load_config(path=CONFIG_FILE):
    config = {"theme": DEFAULT_THEME, "interval": 1}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as stream:
            config.update(yaml.safe_load(stream) or {})
    return config


def theme_dir(config, override=None):
    folder = override or config["theme"]
    return folder if os.path.isabs(folder) else os.path.join(HERE, folder)


def read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return {}


def write_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as stream:
        json.dump(state, stream, indent=2)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def compile_or_exit(folder):
    try:
        return compile_theme(folder)
    except ThemeError as error:
        sys.exit(f"Fout in het thema: {error}")


def check_metrics(compiled, metrics):
    unknown = sorted(m for m in compiled.tags if not metrics.known(m))
    if unknown:
        sys.exit(f"Fout in het thema: onbekende metric(s) {', '.join(unknown)}. "
                 f"Zie 'python smartmonitor.py metrics' of definieer ze onder metrics: in theme.yaml.")


def open_screen(transport, wait=True):
    from library.smartmonitor.transport import ScreenNotFound
    told = False
    while True:
        try:
            transport.open()
            return True
        except ScreenNotFound:
            if not wait:
                return False
            if not told:
                print("Schermpje niet gevonden (USB 0483:0065). Wacht tot het ingeplugd is... (Ctrl+C om te stoppen)")
                told = True
        except OSError as error:
            if not wait:
                raise
            if not told:
                print(f"Schermpje kan niet geopend worden ({error}); is er een ander programma mee bezig? Wacht...")
                told = True
        time.sleep(3)


def upload(transport, compiled, folder):
    started = time.monotonic()

    def progress(stage, block, total):
        if stage == "data":
            if block == 1 or block % 50 == 0 or block == total:
                print(f"\r  thema opladen: blok {block}/{total}", end="", flush=True)
        elif stage == "end of transmission":
            print()
        elif stage == "reset":
            print(f"Thema '{compiled.name}' opladen ({len(compiled.data)} bytes); het schermpje start twee keer "
                  f"opnieuw op...")

    protocol.upload_theme(transport, compiled.data, progress)
    print(f"Thema opgeladen in {time.monotonic() - started:.0f} s.")
    write_state({"uploaded_sha256": digest(compiled.data), "theme": os.path.relpath(folder, HERE),
                 "at": datetime.datetime.now().isoformat(timespec="seconds")})


def cmd_run(args, config):
    from library.smartmonitor.transport import HidTransport
    folder = theme_dir(config, args.theme)
    compiled = compile_or_exit(folder)
    metrics = Metrics(compiled.metrics)
    check_metrics(compiled, metrics)
    interval = max(float(args.interval or config.get("interval", 1)), 0.5)
    transport = HidTransport()
    open_screen(transport)
    print("Schermpje gevonden.")
    if args.upload or read_state().get("uploaded_sha256") != digest(compiled.data):
        upload(transport, compiled, folder)
    else:
        print(f"Thema '{compiled.name}' staat al op het schermpje (gebruik --upload om het opnieuw op te laden).")
    print(f"Waarden worden elke {interval:g} s verstuurd. Stoppen: Ctrl+C of dit venster sluiten.")
    failures = 0
    while True:
        started = time.monotonic()
        values = metrics.read(compiled.tags)
        pairs = [(tag, values[name]) for name, tag in compiled.tags.items() if name in values]
        reports = protocol.value_reports(pairs) + [protocol.clock_report(datetime.datetime.now())]
        try:
            for report in reports:
                transport.write(report)
            if failures:
                print("Verbinding met het schermpje terug.")
            failures = 0
        except OSError as error:
            failures += 1
            if failures == 1:
                print(f"Schermpje reageert niet ({error}). Opnieuw verbinden...")
            if failures == 5:
                print("Blijft het mislukken? Trek het schermpje even uit en steek het terug in.")
            transport.close()
            open_screen(transport)
            continue
        time.sleep(max(0.0, interval - (time.monotonic() - started)))


def preview_values(compiled, metrics):
    current = metrics.read(compiled.tags, wait=True)
    return {tag: protocol.clamp16(current.get(name, SAMPLE_VALUE)) for name, tag in compiled.tags.items()}


def cmd_preview(args, config):
    folder = theme_dir(config, args.theme)
    compiled = compile_or_exit(folder)
    metrics = Metrics(compiled.metrics)
    check_metrics(compiled, metrics)
    metrics.read(compiled.tags, wait=True)
    time.sleep(0.5)  # CPU load and network rates need two measurements
    picture = render_preview(compiled.data, preview_values(compiled, metrics))
    out = args.out or os.path.join(folder, "preview.png")
    picture.save(out)
    print(f"Voorbeeld van '{compiled.name}' ({len(compiled.data)} van max. 503808 bytes): {out}")
    print("Waarden van deze pc; een metric zonder waarde toont " + str(SAMPLE_VALUE) + ".")


def cmd_compile(args, config):
    folder = theme_dir(config, args.theme)
    compiled = compile_or_exit(folder)
    out = args.out or os.path.join(folder, "img.dat")
    with open(out, "wb") as stream:
        stream.write(compiled.data)
    print(f"{out}: {len(compiled.data)} bytes")
    for name, tag in sorted(compiled.tags.items(), key=lambda item: item[1]):
        print(f"  tag {tag:3d}  {name}")


def cmd_upload(args, config):
    from library.smartmonitor.transport import HidTransport
    folder = theme_dir(config, args.theme)
    compiled = compile_or_exit(folder)
    transport = HidTransport()
    if not open_screen(transport, wait=False):
        sys.exit("Schermpje niet gevonden (USB 0483:0065).")
    try:
        upload(transport, compiled, folder)
    finally:
        transport.close()


def cmd_metrics(args, config):
    folder = theme_dir(config, args.theme)
    try:
        compiled = compile_theme(folder)
        custom = compiled.metrics
    except ThemeError:
        custom = {}
    metrics = Metrics(custom)
    names = list(BUILTIN) + list(custom)
    metrics.read(names, wait=True)
    time.sleep(1)
    values = metrics.read(names, wait=True)
    for name in names:
        value = values.get(name)
        shown = f"{value:10.1f}" if value is not None else "         -"
        print(f"{name:18s}{shown}   {BUILTIN.get(name, 'eigen: ' + str(custom.get(name, {}).get('command', '')))}")


def cmd_devices(args, config):
    from library.smartmonitor.transport import find_screens
    screens = find_screens()
    if not screens:
        print("Geen SmartMonitor-schermpje (USB 0483:0065) gevonden.")
    for screen in screens:
        print(f"{screen['manufacturer_string']} / {screen['product_string'].strip()}  serienr. "
              f"{screen['serial_number']}  {screen['path'].decode(errors='replace')}")


def cmd_send(args, config):
    from library.smartmonitor.transport import HidTransport
    pairs = []
    for item in args.pairs:
        tag, _, value = item.partition("=")
        try:
            pairs.append((int(tag), int(value)))
        except ValueError:
            sys.exit(f"'{item}': gebruik tag=waarde, bv. 1=42")
    transport = HidTransport()
    if not open_screen(transport, wait=False):
        sys.exit("Schermpje niet gevonden (USB 0483:0065).")
    try:
        for report in protocol.value_reports(pairs) + [protocol.clock_report(datetime.datetime.now())]:
            transport.write(report)
        print(f"Verstuurd: {', '.join(f'tag {t} = {v}' for t, v in pairs)} + klok")
    finally:
        transport.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Eigen dashboards op het SmartMonitor 3,5\"-schermpje (0483:0065).")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "preview", "upload", "compile", "metrics", "devices", "send"])
    parser.add_argument("pairs", nargs="*", help="bij send: tag=waarde ...")
    parser.add_argument("--theme", help="themamap (standaard: theme uit smartmonitor.yaml)")
    parser.add_argument("--out", help="uitvoerbestand voor preview of compile")
    parser.add_argument("--upload", action="store_true", help="bij run: het thema altijd opnieuw opladen")
    parser.add_argument("--interval", type=float, help="bij run: seconden tussen twee updates")
    args = parser.parse_args(argv)
    config = load_config()
    commands = {"run": cmd_run, "preview": cmd_preview, "upload": cmd_upload, "compile": cmd_compile,
                "metrics": cmd_metrics, "devices": cmd_devices, "send": cmd_send}
    try:
        commands[args.command](args, config)
    except KeyboardInterrupt:
        print("\nGestopt.")
    except protocol.UploadError as error:
        sys.exit(f"{error}\nTrek het schermpje uit, steek het terug in en probeer opnieuw.")


if __name__ == "__main__":
    main()
