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
