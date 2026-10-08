# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""How the kiosk program asks the Electron app to close or restart (web_kiosk._control)."""

from __future__ import annotations

import os
import threading

from nfc_login.kiosk import controller as kc
from nfc_login.ui import web_kiosk
from nfc_login.ui.web_kiosk import WebKiosk


def make(services, monkeypatch, fd=None):
    attendance, _seasons, users = services
    if fd is None:
        monkeypatch.delenv("NFC_KIOSK_CONTROL_FD", raising=False)
    else:
        monkeypatch.setenv("NFC_KIOSK_CONTROL_FD", str(fd))
    return WebKiosk(kc.KioskController(attendance, users), {"leaderboard_size": 10})


def test_close_and_restart_go_down_the_apps_pipe(services, monkeypatch, caplog):
    read_end, write_end = os.pipe()
    kiosk = make(services, monkeypatch, write_end)
    restarted = []
    kiosk.on_restart = lambda: restarted.append(True)
    kiosk.publish(kc.Screen("Closing the kiosk", [], close_app=True), sound=False)
    kiosk.publish(kc.Screen("Restarting the kiosk", [], restart_app=True), sound=False)
    assert os.read(read_end, 100) == b"close\nrestart\n"
    # The app does it: nothing in the log, and this program doesn't restart itself.
    assert "requested" not in caplog.text and restarted == [] and not kiosk.restart_requested
    kiosk.control.close()
    os.close(read_end)


def test_without_the_pipe_the_program_restarts_itself(services, monkeypatch):
    monkeypatch.setattr(web_kiosk, "RESTART_DELAY_SECONDS", 0)
    kiosk = make(services, monkeypatch)
    assert kiosk.control is None
    done = threading.Event()
    kiosk.on_restart = done.set
    kiosk.publish(kc.Screen("Restarting the kiosk", [], restart_app=True), sound=False)
    assert done.wait(2) and kiosk.restart_requested


def test_a_bad_fd_is_ignored(services, monkeypatch):
    assert make(services, monkeypatch, 987).control is None
    monkeypatch.setenv("NFC_KIOSK_CONTROL_FD", "three")
    assert web_kiosk.control_pipe() is None
