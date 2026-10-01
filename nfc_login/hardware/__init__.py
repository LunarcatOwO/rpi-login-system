"""Hardware drivers: PN532 NFC reader, Da Vinci Kit keypad, and simulators."""

from __future__ import annotations


def create_reader(config):
    """Build the NFC reader for ``hardware.mode`` ("pi" or "simulated")."""
    if config.hardware["mode"] == "simulated":
        from nfc_login.hardware.simulated import SimulatedReader
        return SimulatedReader()
    from nfc_login.hardware.nfc_reader import PN532Reader
    return PN532Reader(poll_timeout=config.hardware["nfc"]["poll_timeout_seconds"])


def create_keypad(config):
    """The physical keypad, or None in simulated mode (the PC keyboard is used)."""
    if config.hardware["mode"] == "simulated":
        return None
    from nfc_login.hardware.keypad import MatrixKeypad
    k = config.hardware["keypad"]
    return MatrixKeypad(k["rows"], k["cols"], k["keys"])
