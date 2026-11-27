# SPDX-License-Identifier: GPL-3.0-or-later
#
# The "arcoloo-minischerm:" link: the buttons in ops (Monitor -> Minischerm -> "Op deze pc") start, stop or turn
# "smartmonitor.py ops" on the PC where they are clicked. A web page cannot start programs itself, so
# ops-autostart.ps1 registers this link type once for the Windows user (HKCU, no admin) and Windows then runs
# "pythonw smartmonitor.py link <the link>" when a page opens one.
#
# ANY web page can open such a link, so it accepts only a short fixed list and nothing else:
#   arcoloo-minischerm://start      start the program (when it doesn't run yet)
#   arcoloo-minischerm://opnieuw    stop it and start it again
#   arcoloo-minischerm://stop       stop it
# start and opnieuw may carry ?stand=liggend|staand&omgedraaid=ja|nee&seconden=2..120 (saved, like the options of
# "ops"). The ops address and the screen key never travel in a link: they stay in smartmonitor-ops.json. Only the
# "smartmonitor.py ops" of THIS folder is stopped: another folder, the own dashboard ("run") and other programs are
# left alone.

import os
import re
import subprocess
from urllib.parse import parse_qsl, urlsplit

SCHEME = "arcoloo-minischerm"
VERBS = ("start", "opnieuw", "stop")
MAX_LENGTH = 200
STAND = {"liggend": "landscape", "landscape": "landscape", "staand": "portrait", "portrait": "portrait"}
YES_NO = {"ja": True, "nee": False}
SECONDS_RE = re.compile(r"[0-9]{1,3}")
MIN_SECONDS, MAX_SECONDS = 2, 120


class LinkError(ValueError):
    pass


def parse(url):
    """(verb, settings) from a link; LinkError for anything that is not exactly on the list."""
    text = str(url or "").strip()
    if not text:
        raise LinkError("geen link")
    if len(text) > MAX_LENGTH:
        raise LinkError("link te lang")
    parts = urlsplit(text)
    if parts.scheme.lower() != SCHEME:
        raise LinkError(f"geen {SCHEME}-link")
    verb = (parts.netloc + parts.path).strip("/").lower()
    if verb not in VERBS:
        raise LinkError(f"onbekende opdracht '{verb[:20]}'")
    settings = {}
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True) if parts.query else []
    except ValueError:
        raise LinkError("onleesbare instellingen") from None
    seen = set()
    for name, value in pairs:
        name, value = name.strip().lower(), value.strip().lower()
        if name in seen:
            raise LinkError(f"'{name[:20]}' staat er twee keer in")
        seen.add(name)
        if name == "stand" and value in STAND:
            settings["orientation"] = STAND[value]
        elif name == "omgedraaid" and value in YES_NO:
            settings["upside_down"] = YES_NO[value]
        elif name == "seconden" and SECONDS_RE.fullmatch(value) and MIN_SECONDS <= int(value) <= MAX_SECONDS:
            settings["seconds"] = int(value)
        elif name in ("stand", "omgedraaid", "seconden"):
            raise LinkError(f"waarde voor '{name}' niet toegelaten")
        else:
            raise LinkError(f"onbekende instelling '{name[:20]}'")
    if verb == "stop" and settings:
        raise LinkError("stop neemt geen instellingen")
    return verb, settings


def _same_file(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def ops_processes(program, processes=None):
    """The running "smartmonitor.py ops" of this folder: the window (python) and the one without (pythonw, which
    is two processes in a venv: launcher + interpreter)."""
    import psutil
    if processes is None:
        processes = psutil.process_iter(["pid", "cmdline", "cwd"])
    found = []
    for proc in processes:
        try:
            cmdline = proc.info.get("cmdline") or []
            cwd = proc.info.get("cwd") or ""
        except (AttributeError, psutil.Error):
            continue
        args = list(cmdline[1:])
        for index, arg in enumerate(args):
            if os.path.basename(arg).lower() == "smartmonitor.py":
                if _same_file(os.path.join(cwd, arg), program) and "ops" in args[index + 1:]:
                    found.append(proc)
                break
    return found


def stop(processes, timeout=10):
    """Stop them; True when something was running."""
    import psutil
    if not processes:
        return False
    for proc in processes:
        try:
            proc.terminate()
        except psutil.Error:
            pass
    psutil.wait_procs(processes, timeout=timeout)
    return True


def start(pythonw, program, here, popen=subprocess.Popen):
    """Start "smartmonitor.py ops" without a window, independent of this short-lived handler."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    command = [pythonw, program, "ops"]
    kwargs = dict(cwd=here, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  close_fds=True)
    try:
        # Out of the job of whoever started this handler (a browser), so closing it doesn't take the screen along.
        return popen(command, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **kwargs)
    except OSError:
        return popen(command, creationflags=flags, **kwargs)


def handle(url, *, read_settings, write_settings, running, stop_them, start_it, say):
    """Do what the link asks. Returns 0 when done, 2 when the link is refused, 1 when it can't start."""
    try:
        verb, wanted = parse(url)
    except LinkError as error:
        say(f"Link uit een webpagina geweigerd: {error}.")
        return 2
    settings = read_settings()
    changed = {k: v for k, v in wanted.items() if settings.get(k) != v}
    if changed:
        settings.update(changed)
        write_settings(settings)
        say("Knop in ops: instellingen bewaard (" + ", ".join(f"{k}={v}" for k, v in changed.items()) + ").")
    procs = running()
    if verb == "stop":
        say("Knop in ops: gestopt." if stop_them(procs) else "Knop in ops: stoppen, maar het liep niet.")
        return 0
    if verb == "start" and procs and not changed:
        say("Knop in ops: starten, maar het loopt al.")
        return 0
    if not settings.get("url") or not settings.get("key"):
        say("Knop in ops: eerst instellen (eenmalig) met start-scherm.cmd ops --url ... --key (zie LOKAAL.md).")
        return 1
    again = stop_them(procs)
    start_it()
    say("Knop in ops: opnieuw gestart." if again else "Knop in ops: gestart.")
    return 0
