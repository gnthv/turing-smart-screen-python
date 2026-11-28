# SPDX-License-Identifier: GPL-3.0-or-later
#
# Two programs on one screen. Windows lets several programs open the screen at the same time, so nothing stops this
# program, the ops minischerm in the browser and the Arcoloo kassa from writing to it together. That goes wrong as
# soon as one of them puts a theme on it: the screen restarts into its upload mode ("boot" top left, black), and the
# clock and values another program keeps sending every second break that upload -> the screen hangs in "boot".
#
# So after a write failed while we were not uploading (the screen went away), this program writes nothing until it
# knows what happened. Back and quiet for QUIET_S: a cable that was out for a moment, carry on. Gone again after it
# had been back for STAY_S: it restarted twice, which is what a theme upload does (into its upload mode, and back
# with the new theme) -> another program put its own theme on it.

import time

QUIET_S = 45.0  # a theme upload takes about 20 s: back this long without leaving again = nobody is uploading
STAY_S = 2.0    # back at least this long and then gone again = a second restart, not the same one wobbling
LOOK_S = 0.2    # how often to look whether the screen is plugged in


class ForeignUpload(Exception):
    """The screen restarted twice without us: another program put its own theme on it."""


def wait_after_failure(present, quiet_s=QUIET_S, stay_s=STAY_S, look_s=LOOK_S, sleep=time.sleep,
                       clock=time.monotonic):
    """Write nothing; look every look_s whether the screen is plugged in (present() -> bool).

    Returns when it has been there for quiet_s without going away. Raises ForeignUpload when it goes away again
    after it had been back for stay_s. Windows can still list a screen for a moment after it was pulled out, so a
    screen that is gone again sooner counts as the same restart."""
    there = present()
    back_at = clock() if there else None
    while True:
        if there and clock() - back_at >= quiet_s:
            return
        sleep(look_s)
        now_there, now = present(), clock()
        if now_there and not there:
            back_at = now
        elif there and not now_there:
            if now - back_at >= stay_s:
                raise ForeignUpload()
            back_at = None
        there = now_there
