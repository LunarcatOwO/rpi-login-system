# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Buzzer patterns, and which kiosk events play which pattern."""

import time

import pytest

from nfc_login.config import load_config
from nfc_login.hardware.buzzer import PATTERNS, Buzzer
from nfc_login.kiosk.controller import Screen


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, on):
        self.calls.append(on)


def wait_quiet(buzzer, pattern):
    time.sleep(sum(buzzer.patterns[pattern]) / 1000 + 0.2)


def test_pattern_alternates_on_and_off_then_ends_off():
    out = Recorder()
    buzzer = Buzzer(out, patterns={"short": [10, 10, 10]})
    buzzer.play("short")
    wait_quiet(buzzer, "short")
    assert out.calls == [True, False, True, False]


def test_new_pattern_cuts_off_the_old_one():
    out = Recorder()
    buzzer = Buzzer(out, patterns={"long": [2000], "short": [10]})
    buzzer.play("long")
    time.sleep(0.05)
    buzzer.play("short")
    wait_quiet(buzzer, "short")
    assert out.calls[-2:] == [True, False] and out.calls.count(True) == 2


def test_key_clicks_can_be_turned_off_and_unknown_names_ignored():
    out = Recorder()
    buzzer = Buzzer(out, key_clicks=False)
    buzzer.play("key")
    buzzer.play("no-such-pattern")
    buzzer.play(None)
    time.sleep(0.1)
    assert out.calls == []


def test_every_pattern_starts_on_and_is_short():
    for name, pattern in PATTERNS.items():
        assert len(pattern) % 2 == 1, name           # ends on a beep, not a gap
        assert 0 < sum(pattern) <= 1000, name


def test_screen_sounds_follow_tone_unless_set():
    assert Screen("x", tone="error").buzz == "error"
    assert Screen("x", tone="success").buzz == "success"
    assert Screen("x", tone="prompt").buzz is None
    assert Screen("x", tone="success", sound="sign_in").buzz == "sign_in"


def test_buzzer_pin_must_not_clash():
    from nfc_login.hardware import create_buzzer
    config = load_config("/nonexistent.toml")
    config.hardware["buzzer"]["pin"] = 18          # a keypad row
    with pytest.raises(ValueError, match="buzzer pin 18"):
        create_buzzer(config)
    config.hardware["buzzer"]["enabled"] = False
    assert create_buzzer(config) is None
