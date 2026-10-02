"""Hardware drivers: the PN532 NFC reader, the keypad, the buzzer and a simulator."""

from __future__ import annotations


def create_reader(config):
    """The PN532 reader, or a simulated one when ``hardware.mode = "simulated"``."""
    if config.hardware["mode"] == "simulated":
        from nfc_login.hardware.simulated import SimulatedReader
        return SimulatedReader()
    from nfc_login.hardware.nfc_reader import PN532Reader
    return PN532Reader(poll_timeout=config.hardware["nfc"]["poll_timeout_seconds"])


def create_keypad(config):
    """The physical keypad, or None in simulated mode or when disabled."""
    k = config.hardware["keypad"]
    if config.hardware["mode"] == "simulated" or not k["enabled"]:
        return None
    from nfc_login.hardware.keypad import MatrixKeypad
    return MatrixKeypad(k["rows"], k["cols"], k["keys"])


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
