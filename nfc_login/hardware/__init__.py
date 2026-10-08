# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Hardware drivers: the PN532 NFC reader, the keypad, the buzzer and a simulator."""

from __future__ import annotations

# BCM pins each PN532 wiring uses. SPI0 includes CE0 (GPIO 8): the kernel's SPI
# driver holds it even though the PN532's chip select is on its own pin.
SPI_PINS = {8, 9, 10, 11}
I2C_PINS = {2, 3}
UART_PINS = {14, 15}


def reader_pins(config) -> set[int]:
    nfc = config.hardware["nfc"]
    interface = nfc.get("interface", "spi")
    if interface == "spi":
        return SPI_PINS | {nfc.get("spi_cs_pin", 5)}
    return I2C_PINS if interface == "i2c" else UART_PINS


def create_reader(config):
    """The PN532 reader, or a simulated one when ``hardware.mode = "simulated"``."""
    if config.hardware["mode"] == "simulated":
        from nfc_login.hardware.simulated import SimulatedReader
        return SimulatedReader()
    from nfc_login.hardware.nfc_reader import PN532Reader
    nfc = config.hardware["nfc"]
    return PN532Reader(poll_timeout=nfc["poll_timeout_seconds"], nfc=nfc,
                       tries=nfc.get("tries", 4))


def create_keypad(config):
    """The physical keypad, or None in simulated mode or when disabled."""
    k = config.hardware["keypad"]
    if config.hardware["mode"] == "simulated" or not k["enabled"]:
        return None
    clash = set(k["rows"] + k["cols"]) & reader_pins(config)
    if clash:
        raise ValueError(f"keypad pin(s) {sorted(clash)} are wired to the PN532 "
                         f"({config.hardware['nfc'].get('interface', 'spi')}); "
                         "move them in [hardware.keypad]")
    from nfc_login.hardware.keypad import MatrixKeypad
    return MatrixKeypad(k["rows"], k["cols"], k["keys"])


def create_buzzer(config):
    """The buzzer, or None in simulated mode or when disabled."""
    b = config.hardware["buzzer"]
    if config.hardware["mode"] == "simulated" or not b["enabled"]:
        return None
    k = config.hardware["keypad"]
    taken = reader_pins(config) | (set(k["rows"] + k["cols"]) if k["enabled"] else set())
    if b["pin"] in taken:
        raise ValueError(f"buzzer pin {b['pin']} is already used by the keypad or the "
                         "PN532; pick another in [hardware.buzzer]")
    from nfc_login.hardware.buzzer import Buzzer, GpioBuzzerOutput
    output = GpioBuzzerOutput(b["pin"], b["type"], b["frequency"], b["active_low"])
    return Buzzer(output, b["patterns"], b["key_clicks"])
