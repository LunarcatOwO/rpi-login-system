# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""NFC login system for a Raspberry Pi 4 B kiosk.

Packages:
    nfc_login.config     - loads config.toml
    nfc_login.db         - MariaDB schema, connection and queries
    nfc_login.hardware   - PN532 NFC reader, matrix keypad, simulators
    nfc_login.tags       - NDEF encoding and the data written to each card
    nfc_login.services   - attendance, leaderboard and season logic
    nfc_login.kiosk      - controller tying card scans and keypad input together
    nfc_login.ui         - Tkinter touchscreen interface
    nfc_login.admin      - command-line admin tool
"""

__version__ = "0.1.0"
