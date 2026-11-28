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
#   python smartmonitor.py ops             Arcoloo ops: every shop + the gateway in turn (see LOKAAL.md, "Arcoloo ops")
#   python smartmonitor.py ops-preview     draw that screen as ops-preview.png (no screen needed)
#   python smartmonitor.py link <link>     what Windows runs for the Start/Stop buttons in ops (see ops-autostart.ps1)

import argparse
import atexit
import datetime
import getpass
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
OPS_FILE = os.path.join(HERE, "smartmonitor-ops.json")  # address of ops + screen key: local, never in git
# SMARTMONITOR_OPS_LOG: only for the tests (a link can't set environment variables)
OPS_LOG = os.environ.get("SMARTMONITOR_OPS_LOG") or os.path.join(HERE, "smartmonitor-ops.log")
LOCK_FILE = os.path.join(HERE, "smartmonitor.lock")
DEFAULT_THEME = "res/smartmonitor/themes/standaard"
SAMPLE_VALUE = 88
ORIENTATION_WORDS = {"landscape": "landscape", "liggend": "landscape", "portrait": "portrait", "staand": "portrait"}


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


def open_screen(transport, wait=True, say=print):
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
                say("Schermpje niet gevonden (USB 0483:0065). Wacht tot het ingeplugd is... (Ctrl+C om te stoppen)")
                told = True
        except OSError as error:
            if not wait:
                raise
            if not told:
                say(f"Schermpje kan niet geopend worden ({error}); is er een ander programma mee bezig? Wacht...")
                told = True
        time.sleep(3)


def take_lock(say=print):
    """One program on the screen at a time: two writers garble the theme upload and fight over the values."""
    import psutil
    try:
        with open(LOCK_FILE, encoding="utf-8") as stream:
            pid = int(stream.read().strip() or 0)
    except (OSError, ValueError):
        pid = 0
    if pid and pid != os.getpid() and psutil.pid_exists(pid):
        try:
            command = " ".join(psutil.Process(pid).cmdline())
        except psutil.Error:
            command = ""
        if "smartmonitor.py" in command:
            message = (f"Er draait al een smartmonitor.py (proces {pid}): twee programma's tegelijk verknoeien het "
                       f"schermpje. Stop het andere eerst (venster sluiten of Ctrl+C).")
            say(message)
            sys.exit(message)
    with open(LOCK_FILE, "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))

    def release():
        try:
            with open(LOCK_FILE, encoding="utf-8") as stream:
                mine = stream.read().strip() == str(os.getpid())
            if mine:
                os.remove(LOCK_FILE)
        except OSError:
            pass
    atexit.register(release)


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
    take_lock()
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
    take_lock()
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
    take_lock()
    transport = HidTransport()
    if not open_screen(transport, wait=False):
        sys.exit("Schermpje niet gevonden (USB 0483:0065).")
    try:
        for report in protocol.value_reports(pairs) + [protocol.clock_report(datetime.datetime.now())]:
            transport.write(report)
        print(f"Verstuurd: {', '.join(f'tag {t} = {v}' for t, v in pairs)} + klok")
    finally:
        transport.close()


# ---------------------------------------------------------------- Arcoloo ops ("ops", "ops-preview")

OPS_DEFAULTS = {"url": "", "key": "", "seconds": 5, "orientation": "landscape", "upside_down": False}
TEST_NAMES = ["Bakkerij Voorbeeld", "Drinks & Drive", "ARCOLOO-GW"]


def read_ops_settings():
    settings = dict(OPS_DEFAULTS)
    try:
        with open(OPS_FILE, encoding="utf-8") as stream:
            saved = json.load(stream)
        if isinstance(saved, dict):
            settings.update({k: saved[k] for k in OPS_DEFAULTS if k in saved})
    except (OSError, ValueError):
        pass
    return settings


def ops_settings(args):
    """The saved settings with what was given on the command line; (settings, changed)."""
    from library.smartmonitor import ops
    saved = read_ops_settings()
    settings = dict(saved)
    try:
        if args.url is not None:
            settings["url"] = ops.normalize_url(args.url)
        if args.key is not None:
            key = args.key or getpass.getpass("Plak de schermsleutel (msk_..., je ziet ze niet verschijnen): ")
            settings["key"] = ops.check_key(key)
        if args.seconds is not None:
            settings["seconds"] = ops.norm_seconds(args.seconds)
        if args.orientation is not None:
            settings["orientation"] = ORIENTATION_WORDS[args.orientation]
        if args.upside_down is not None:
            settings["upside_down"] = args.upside_down
    except ValueError as error:
        sys.exit(f"Instelling niet goed: {error}")
    settings["seconds"] = ops.norm_seconds(settings["seconds"])
    if settings["orientation"] not in ops.ORIENTATIONS:
        settings["orientation"] = "landscape"
    settings["upside_down"] = bool(settings["upside_down"])
    return settings, settings != saved


def write_ops_settings(settings):
    with open(OPS_FILE, "w", encoding="utf-8") as stream:
        json.dump(settings, stream, indent=2)


def ops_logger():
    """Lines go to the window (when there is one) and to smartmonitor-ops.log (when it runs without a window)."""
    import logging
    import logging.handlers
    log = logging.getLogger("smartmonitor.ops")
    if not log.handlers:
        log.setLevel(logging.INFO)
        log.propagate = False
        try:
            to_file = logging.handlers.RotatingFileHandler(OPS_LOG, maxBytes=200_000, backupCount=1, encoding="utf-8")
            to_file.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
            log.addHandler(to_file)
        except OSError:
            pass
        if sys.stdout is not None:
            to_window = logging.StreamHandler(sys.stdout)
            to_window.setFormatter(logging.Formatter("%(message)s"))
            log.addHandler(to_window)
    return log.info


def describe_ops(status, poller=None):
    if status == 200:
        from library.smartmonitor import ops
        targets = ops.targets_of(poller.snapshot().get("payload")) if poller else []
        shops = sum(1 for t in targets if t.get("kind") != "gateway")
        gateway = " + gateway" if len(targets) > shops else ""
        red = sum(1 for t in targets if t.get("overall") == "bad")
        return f"Verbonden met ops: {shops} winkel(s){gateway}" + (f", {red} in het rood." if red else ".")
    if status == 401:
        return "Ops weigert de schermsleutel (ingetrokken?). Maak een nieuwe in ops: Monitor -> Minischerm."
    if status == 403:
        return "Ops weigert dit netwerk (IP-allowlist van ops). Het schermpje toont na 3 min 'Geen verbinding'."
    if status == 0:
        return "Ops is niet bereikbaar; elke 30 s opnieuw proberen."
    return f"Ops antwoordt met fout {status}; elke 30 s opnieuw proberen."


class OpsStore:
    """Which theme is on the screen, in smartmonitor-state.json (shared with 'run': it uploads its own theme back)."""

    def get_digest(self):
        return read_state().get("uploaded_sha256")

    def set_digest(self, sha256, names, labels):
        write_state({"uploaded_sha256": sha256, "theme": "ops", "ops_names": names, "ops_labels": labels,
                     "at": datetime.datetime.now().isoformat(timespec="seconds")})

    def last_names(self):
        state = read_state()
        return state.get("ops_names") if state.get("theme") == "ops" else None

    def last_labels(self):
        state = read_state()
        return state.get("ops_labels") if state.get("theme") == "ops" else None


def cmd_ops(args, config):
    from library.smartmonitor import ops
    from library.smartmonitor.transport import HidTransport
    settings, changed = ops_settings(args)
    if changed:
        write_ops_settings(settings)
        print(f"Instellingen bewaard in {os.path.basename(OPS_FILE)}.")
    if not settings["url"] or not settings["key"]:
        sys.exit("Eerst instellen (eenmalig): start-scherm.cmd ops "
                 "--url https://arcoloomonitoring.up.railway.app --key\n"
                 "(de sleutel maak je in ops: Monitor -> Minischerm -> Sleutel voor een pc-programma)")
    say = ops_logger()
    take_lock(say)
    turned = settings["orientation"] + (", omgedraaid" if settings["upside_down"] else "")
    say(f"Arcoloo ops op het schermpje: {settings['url']}, sleutel {ops.masked(settings['key'])}, elke "
        f"{settings['seconds']} s een andere winkel, {turned}. Stoppen: Ctrl+C of dit venster sluiten.")
    poller = ops.Poller(settings["url"], settings["key"])
    last_status = poller.poll_once()
    say(describe_ops(last_status, poller))
    poller.start()
    transport = HidTransport()
    open_screen(transport, say=say)
    say("Schermpje gevonden.")
    screen = ops.OpsScreen(transport, poller, OpsStore(), settings["seconds"], settings["orientation"],
                           settings["upside_down"], say=say)
    force, failures = bool(args.upload), 0
    while True:
        started = time.monotonic()
        try:
            if force:
                screen.ensure_theme(poller.snapshot(), force=True)
                force = False
            screen.tick(datetime.datetime.now())
            if failures:
                say("Verbinding met het schermpje terug.")
            failures = 0
        except protocol.UploadError as error:
            say(f"{error}. Over 15 s opnieuw (trek het schermpje even uit als het blijft mislukken).")
            transport.close()
            time.sleep(15)
            open_screen(transport, say=say)
            continue
        except OSError as error:
            failures += 1
            if failures == 1:
                say(f"Schermpje reageert niet ({error}). Opnieuw verbinden...")
            if failures == 5:
                say("Blijft het mislukken? Trek het schermpje even uit en steek het terug in.")
            transport.close()
            open_screen(transport, say=say)
            continue
        if poller.last_status != last_status:
            last_status = poller.last_status
            say(describe_ops(last_status, poller))
        time.sleep(max(0.0, 1.0 - (time.monotonic() - started)))


def cmd_ops_preview(args, config):
    from library.smartmonitor import ops
    settings, _ = ops_settings(args)
    moment = datetime.datetime.now()
    if args.test:
        names, labels, view, line = TEST_NAMES, list(ops.TILE_LABELS), ops.TEST_VIEW, "testbeeld"
    else:
        if not settings["url"] or not settings["key"]:
            sys.exit("Eerst instellen: start-scherm.cmd ops --url ... --key (of gebruik --test)")
        poller = ops.Poller(settings["url"], settings["key"])
        status = poller.poll_once()
        state = poller.snapshot()
        payload = state.get("payload")
        names = ops.name_list(ops.targets_of(payload)) if payload else (OpsStore().last_names() or [])
        labels = ops.tile_labels(payload)
        view = ops.view_for(state, time.time() * 1000, settings["seconds"])
        line = describe_ops(status, poller)
    data = ops.build_status_theme(names, labels, settings["orientation"], settings["upside_down"])
    turn = ops.Orientation(settings["orientation"], settings["upside_down"])
    picture = turn.unturn(render_preview(data, dict(ops.tag_pairs(view, moment)), moment))
    out = args.out or os.path.join(HERE, "ops-preview.png")
    picture.save(out)
    print(f"Voorbeeld ({settings['orientation']}{', omgedraaid' if settings['upside_down'] else ''}, "
          f"{len(data)} van max. 503808 bytes): {out}")
    print(line)


def cmd_link(args, config):
    """Windows runs this for an arcoloo-minischerm: link from the ops page (ops-autostart.ps1 registers it)."""
    from library.smartmonitor import link
    program = os.path.join(HERE, "smartmonitor.py")
    pythonw = os.path.join(HERE, ".venv", "Scripts", "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    say = ops_logger()
    try:
        code = link.handle(args.pairs[0] if len(args.pairs) == 1 else "",
                           read_settings=read_ops_settings, write_settings=write_ops_settings,
                           running=lambda: link.ops_processes(program),
                           stop_them=link.stop,
                           start_it=lambda: link.start(pythonw, program, HERE),
                           say=say)
    except Exception as error:  # pythonw has no window: without this line a failed click leaves no trace
        say(f"Knop in ops: mislukt ({type(error).__name__}: {link.shown(error, 200)}).")
        code = 1
    if code:
        sys.exit(code)


def yes_no(text):
    word = str(text).strip().lower()
    if word in ("ja", "yes", "aan", "on", "1", "true"):
        return True
    if word in ("nee", "no", "uit", "off", "0", "false"):
        return False
    raise argparse.ArgumentTypeError("ja of nee")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Eigen dashboards op het SmartMonitor 3,5\"-schermpje (0483:0065).")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "preview", "upload", "compile", "metrics", "devices", "send", "ops",
                                 "ops-preview", "link"])
    parser.add_argument("pairs", nargs="*", help="bij send: tag=waarde ...; bij link: de arcoloo-minischerm-link")
    parser.add_argument("--theme", help="themamap (standaard: theme uit smartmonitor.yaml)")
    parser.add_argument("--out", help="uitvoerbestand voor preview, ops-preview of compile")
    parser.add_argument("--upload", action="store_true", help="bij run en ops: het thema altijd opnieuw opladen")
    parser.add_argument("--interval", type=float, help="bij run: seconden tussen twee updates")
    parser.add_argument("--url", help="bij ops: adres van ops, bv. https://arcoloomonitoring.up.railway.app (bewaard)")
    parser.add_argument("--key", nargs="?", const="",
                        help="bij ops: de schermsleutel msk_... (bewaard); zonder waarde vraagt het ernaar")
    parser.add_argument("--seconds", type=int, help="bij ops: seconden per winkel, 2-120 (standaard 5, bewaard)")
    parser.add_argument("--orientation", choices=sorted(ORIENTATION_WORDS),
                        help="bij ops: liggend (landscape) of staand (portrait) (bewaard)")
    parser.add_argument("--upside-down", type=yes_no, metavar="ja|nee",
                        help="bij ops: het beeld omgedraaid (180 graden) (bewaard)")
    parser.add_argument("--test", action="store_true", help="bij ops-preview: een testbeeld zonder ops")
    args = parser.parse_args(argv)
    config = load_config()
    commands = {"run": cmd_run, "preview": cmd_preview, "upload": cmd_upload, "compile": cmd_compile,
                "metrics": cmd_metrics, "devices": cmd_devices, "send": cmd_send, "ops": cmd_ops,
                "ops-preview": cmd_ops_preview, "link": cmd_link}
    try:
        commands[args.command](args, config)
    except KeyboardInterrupt:
        print("\nGestopt.")
    except protocol.UploadError as error:
        sys.exit(f"{error}\nTrek het schermpje uit, steek het terug in en probeer opnieuw.")


if __name__ == "__main__":
    main()
