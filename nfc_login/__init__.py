# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""NFC login system for a Raspberry Pi 4 B kiosk."""

# Packages, from the bottom up:
#   config    loads config.toml
#   db        MariaDB schema, connection and every SQL query
#   hardware  PN532 NFC reader, 4x4 keypad, buzzer, simulator
#   tags      NDEF encoding and what gets written onto each card
#   services  attendance, leaderboard, seasons, users, updates, system
#   kiosk     the controller: what a card scan or key press does
#   ui        the kiosk screen (Tkinter window or the Electron app's web page)
#   web       live "who's here" page and admin page on port 8080
#   admin     command-line admin tool

__version__ = "0.1.0"
