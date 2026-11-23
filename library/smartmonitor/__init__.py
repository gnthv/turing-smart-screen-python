# SPDX-License-Identifier: GPL-3.0-or-later
#
# SmartMonitor 3.5" HID screen (USB 0483:0065, "llhmi.com" / "llTechCo.,Ltd", sold as "3.5 inch PC CPU Data Monitor").
#
# Unlike the Turing / XuanFang screens this one is NOT a framebuffer: it plays a theme stored in its own flash
# (background, pre-rendered glyphs and widgets bound to numeric tags). The computer uploads the theme once and then
# only sends whole numbers and the clock.
#
# The protocol and the theme format are implemented from the public description in the sensordeck project
# (https://github.com/norareit/sensordeck, MIT, docs/protocol.md and docs/theme-format.md). No vendor file is used.
