"""Configuration loading.

Settings live in a TOML file (``config.toml`` by default, see
``config.example.toml``). Anything missing falls back to the defaults below,
so a config file only needs the values you want to change.
"""

from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(os.environ.get("NFC_LOGIN_CONFIG", "config.toml"))

DEFAULTS: dict = {
    "database": {
        "host": "localhost",
        "port": 3306,
        "user": "nfc_login",
        "password": "",
        "name": "nfc_login",
    },
    "hardware": {
        # "pi" uses the real PN532 + keypad, "simulated" runs on any computer.
        "mode": "pi",
        # PN532 wiring. SPI is the quickest and most reliable on a Pi 4 (see
        # docs/hardware.md); "i2c" and "uart" also work if it's wired that way.
        "nfc": {
            "interface": "spi",
            "spi_cs_pin": 5,           # BCM pin for the PN532's SS (physical pin 29)
            "spi_baudrate": 1_000_000, # Hz; the PN532 allows up to 5 MHz
            "uart_port": "/dev/serial0",
            "tries": 4,                # attempts per block if a read or write fails
            "write_tags": True,
            "poll_timeout_seconds": 0.5,
        },
        # Buzzer on GPIO 12 (physical pin 32), clear of the keypad and PN532 pins.
        "buzzer": {
            "enabled": True,
            "pin": 12,
            "type": "active",          # "active" (beeps on its own) or "passive" (needs a tone)
            "frequency": 2700,         # Hz, passive buzzers only
            "active_low": False,       # True for modules that beep when the pin is LOW
            "key_clicks": True,        # short click on every keypad press
            "patterns": {},            # override a pattern, e.g. sign_in = [70, 50, 180]
        },
        "keypad": {
            "enabled": True,
            # BCM pin numbers: the Da Vinci Kit "2.1.5 Keypad" lesson's, except
            # its GPIO 10 (SPI MOSI, needed by the PN532) moves to GPIO 6.
            "rows": [18, 23, 24, 25],
            "cols": [6, 22, 27, 17],
            "keys": [
                "1", "2", "3", "A",
                "4", "5", "6", "B",
                "7", "8", "9", "C",
                "*", "0", "#", "D",
            ],
            "poll_interval_seconds": 0.05,
        },
    },
    "tag": {
        # Placeholder: the GitHub Pages site that will read the card on a phone.
        "site_url": "https://lunarcatowo.github.io/rpi-login-system/",
    },
    "attendance": {
        # A second scan of the same card within this window is ignored.
        "min_scan_interval_seconds": 10,
        # Sessions left open longer than this are closed with no time credited.
        "max_session_hours": 12,
    },
    "ui": {
        "fullscreen": True,
        "width": 800,
        "height": 480,
        "leaderboard_size": 10,
        "result_seconds": 6,
        "keypad_timeout_seconds": 30,
    },
    "seasons": {
        "archive_dir": "archive",
    },
    # Teams, one per keypad letter key. The letter starts each user ID (A007,
    # D012); "key" is the keypad key that types it and "short" fits the
    # kiosk's side panel. Letter M is the mentors: no key, number-only IDs (007).
    "sections": [
        {"letter": "A", "name": "Robot", "short": "Robot", "key": "A"},
        {"letter": "B", "name": "Impact", "short": "Impact", "key": "B"},
        {"letter": "C", "name": "Sustainability", "short": "Sustain", "key": "C"},
        {"letter": "D", "name": "Strategy", "short": "Strategy", "key": "D"},
        {"letter": "M", "name": "Mentors", "short": "Mentor", "key": ""},
    ],
    # Live "who's here" page and admin page, served by the kiosk on the LAN.
    "web": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8080,
        "refresh_seconds": 3,
    },
}


def _merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@dataclass
class Config:
    data: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS))

    def section(self, *path: str) -> dict:
        node = self.data
        for key in path:
            node = node[key]
        return node

    @property
    def database(self) -> dict:
        return self.data["database"]

    @property
    def hardware(self) -> dict:
        return self.data["hardware"]

    @property
    def tag(self) -> dict:
        return self.data["tag"]

    @property
    def attendance(self) -> dict:
        return self.data["attendance"]

    @property
    def ui(self) -> dict:
        return self.data["ui"]

    @property
    def seasons(self) -> dict:
        return self.data["seasons"]

    @property
    def sections(self) -> list[dict]:
        return self.data["sections"]

    @property
    def web(self) -> dict:
        return self.data["web"]


def load_config(path: str | Path | None = None) -> Config:
    """Load the config file, merged over the defaults.

    A missing file is not an error: the defaults are used, which is handy for
    ``hardware.mode = "simulated"`` development runs.
    """
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    override: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            override = tomllib.load(fh)
    data = _merge(DEFAULTS, override)
    _check_sections(data["sections"])
    # Environment variables win for secrets so they can stay out of the file.
    if os.environ.get("NFC_LOGIN_DB_PASSWORD"):
        data["database"]["password"] = os.environ["NFC_LOGIN_DB_PASSWORD"]
    return Config(data)


def _check_sections(sections: list[dict]) -> None:
    from nfc_login.services.ids import MENTORS, UNSORTED

    for section in sections:
        section["letter"] = section["letter"].upper()
        section["key"] = section.get("key", "").upper()
        section.setdefault("short", section["name"][:8])
        letter, key = section["letter"], section["key"]
        if len(letter) != 1 or not letter.isalpha():
            raise ValueError(f"section letter must be one letter: {letter!r}")
        if letter == UNSORTED:
            raise ValueError(f"section letter {UNSORTED} is reserved for imported users "
                             "who haven't been given a team yet")
        if letter == MENTORS:
            if key:
                raise ValueError(f"section {MENTORS} is the mentors, whose IDs are only "
                                 "numbers, so it can't have a keypad key")
        elif len(key) != 1 or key == "*" or key.isdigit():
            raise ValueError(f"section {letter} needs one keypad key that isn't * or a digit "
                             "(those are used for typing)")
    letters = [s["letter"] for s in sections]
    keys = [s["key"] for s in sections if s["key"]]
    if len(set(letters)) != len(letters) or len(set(keys)) != len(keys):
        raise ValueError("each section needs its own letter and its own keypad key")
