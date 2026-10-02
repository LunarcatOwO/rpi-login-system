"""Hardware drivers: NFC readers (PN532, legacy MFRC522), keypad, LCD, buzzer, simulators."""

from __future__ import annotations


def create_reader(config):
    """Build the card reader for ``hardware.mode`` and ``hardware.nfc.reader``."""
    if config.hardware["mode"] == "simulated":
        from nfc_login.hardware.simulated import SimulatedReader
        return SimulatedReader()
    nfc = config.hardware["nfc"]
    if nfc["reader"] == "mfrc522":
        from nfc_login.hardware.mfrc522_reader import MFRC522Reader
        m = nfc["mfrc522"]
        return MFRC522Reader(m["spi_bus"], m["spi_device"], m["rst_pin"])
    if nfc["reader"] != "pn532":
        raise ValueError(f"unknown hardware.nfc.reader {nfc['reader']!r} (pn532 or mfrc522)")
    from nfc_login.hardware.nfc_reader import PN532Reader
    return PN532Reader(poll_timeout=nfc["poll_timeout_seconds"])


def create_keypad(config):
    """The physical keypad, or None in simulated mode or when disabled."""
    k = config.hardware["keypad"]
    if config.hardware["mode"] == "simulated" or not k["enabled"]:
        return None
    nfc = config.hardware["nfc"]
    if nfc["reader"] == "mfrc522":
        # The RC522 uses SPI0 (GPIO 7-11) plus its reset pin.
        taken = {7, 8, 9, 10, 11, nfc["mfrc522"]["rst_pin"]}
        clash = sorted(taken & set(k["rows"] + k["cols"]))
        if clash:
            raise ValueError(
                f"keypad pins {clash} are used by the RC522 reader; move the keypad "
                "(see docs/legacy.md) or set [hardware.keypad] enabled = false")
    from nfc_login.hardware.keypad import MatrixKeypad
    return MatrixKeypad(k["rows"], k["cols"], k["keys"])


def create_lcd(config):
    """The 16x2 LCD if enabled (always on in headless mode), else None."""
    lcd = config.hardware["lcd"]
    if config.hardware["mode"] == "simulated":
        return None
    if not (lcd["enabled"] or config.ui["mode"] == "headless"):
        return None
    from nfc_login.hardware.lcd1602 import Lcd1602
    return Lcd1602(lcd["i2c_bus"], int(lcd["address"]))


I2C_PINS = {2, 3}   # the PN532


def create_buzzer(config):
    """The buzzer, or None in simulated mode or when disabled."""
    b = config.hardware["buzzer"]
    if config.hardware["mode"] == "simulated" or not b["enabled"]:
        return None
    k = config.hardware["keypad"]
    taken = I2C_PINS | (set(k["rows"] + k["cols"]) if k["enabled"] else set())
    if b["pin"] in taken:
        raise ValueError(f"buzzer pin {b['pin']} is already used by the keypad or the "
                         "PN532; pick another in [hardware.buzzer]")
    from nfc_login.hardware.buzzer import Buzzer, GpioBuzzerOutput
    output = GpioBuzzerOutput(b["pin"], b["type"], b["frequency"], b["active_low"])
    return Buzzer(output, b["patterns"], b["key_clicks"])
