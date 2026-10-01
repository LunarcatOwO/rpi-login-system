"""Hardware drivers: the PN532 NFC reader, the keypad and a simulator."""

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
