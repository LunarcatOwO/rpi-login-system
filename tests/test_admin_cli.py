# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""set-admin-pin on the command line: easy the first time, guarded after."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nfc_login.admin import cli
from nfc_login.services.users import UserError


def run(services, monkeypatch, pins, forgot=False, root=False, typed="RESET"):
    """Run set-admin-pin with the PINs it will be asked for, in order."""
    _attendance, _seasons, users = services
    answers = iter(pins)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0 if root else 1000)
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)
    cli.cmd_set_admin_pin(SimpleNamespace(users=users), SimpleNamespace(forgot=forgot), None)
    return users


def test_first_pin_needs_nothing(services, monkeypatch):
    users = run(services, monkeypatch, ["2468", "2468"])
    assert users.check_admin_pin("2468")


def test_changing_needs_the_current_pin(services, monkeypatch):
    services[2].set_admin_pin("2468")
    with pytest.raises(UserError, match="Wrong admin PIN"):
        run(services, monkeypatch, ["1111", "1357", "1357"])
    users = run(services, monkeypatch, ["2468", "1357", "1357"])
    assert users.check_admin_pin("1357")


def test_wrong_tries_lock_it_even_across_runs(services, monkeypatch, clock):
    services[2].set_admin_pin("2468")
    for _ in range(3):
        with pytest.raises(UserError, match="Wrong admin PIN"):
            run(services, monkeypatch, ["1111"])
    with pytest.raises(UserError, match="Try again in 15 minute"):
        run(services, monkeypatch, ["2468", "1357", "1357"])  # even the right PIN
    clock.advance(minutes=15)
    assert run(services, monkeypatch, ["2468", "1357", "1357"]).check_admin_pin("1357")


def test_forgot_needs_root_and_reset(services, monkeypatch):
    services[2].set_admin_pin("2468")
    with pytest.raises(UserError, match="needs sudo"):
        run(services, monkeypatch, ["1357", "1357"], forgot=True)
    with pytest.raises(UserError, match="Not reset"):
        run(services, monkeypatch, ["1357", "1357"], forgot=True, root=True, typed="yes")
    users = run(services, monkeypatch, ["1357", "1357"], forgot=True, root=True)
    assert users.check_admin_pin("1357")
