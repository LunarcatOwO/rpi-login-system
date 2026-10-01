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
        "nfc": {
            "write_tags": True,
            "poll_timeout_seconds": 0.5,
        },
        "keypad": {
            # BCM pin numbers, matching the Da Vinci Kit "2.1.5 Keypad" lesson.
            "rows": [18, 23, 24, 25],
            "cols": [10, 22, 27, 17],
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
    # Environment variables win for secrets so they can stay out of the file.
    if os.environ.get("NFC_LOGIN_DB_PASSWORD"):
        data["database"]["password"] = os.environ["NFC_LOGIN_DB_PASSWORD"]
    return Config(data)
