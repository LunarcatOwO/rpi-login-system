# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Compatibility with the legacy attendance system (github.com/aesom-e/attendance).

That system used an MFRC522 (RC522) RFID reader, a 16x2 I2C LCD and a PHP +
MariaDB backend. This package converts its card numbers and imports its data.
"""
