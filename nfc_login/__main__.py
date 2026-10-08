# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Start the kiosk:  python3 -m nfc_login [--config config.toml] [--simulate] [--web-ui]"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from nfc_login.app import build_services
from nfc_login.config import load_config
from nfc_login.hardware import create_buzzer, create_keypad, create_reader
from nfc_login.hardware.keypad import KeypadPoller
from nfc_login.kiosk.controller import KioskController
from nfc_login.kiosk.nfc_worker import NfcWorker
from nfc_login.services.system import SystemActions
from nfc_login.services.updates import UpdateChecker
from nfc_login.web.server import start_in_background


def main() -> None:
    """Read the command line, build the services and run the kiosk until it closes."""
    parser = argparse.ArgumentParser(description="NFC sign-in kiosk")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--simulate", action="store_true",
                        help="run without the NFC reader and keypad")
    parser.add_argument("--windowed", action="store_true", help="don't go fullscreen")
    parser.add_argument("--web-ui", type=int, nargs="?", const=8081, metavar="PORT",
                        help="serve the kiosk screen at http://127.0.0.1:PORT/ "
                             "(default 8081) instead of opening a window")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    # Command-line flags win over config.toml.
    if args.simulate:
        config.hardware["mode"] = "simulated"
    if args.windowed:
        config.ui["fullscreen"] = False

    services = build_services(config)
    # There must always be an active season to credit hours to.
    season = services.seasons.ensure_active()
    logging.info("active season: %s", season["name"])

    # Hardware (or stand-ins in simulated mode). keypad/buzzer are None when off.
    reader = create_reader(config)
    keypad = create_keypad(config)
    buzzer = create_buzzer(config)
    logging.info("NFC reader: %s", reader.firmware_version())
    logging.info("keypad: %s", "ready" if keypad else "off")
    logging.info("buzzer: %s", "ready" if buzzer else "off")

    # The admin menu can always check for and install updates by hand; with
    # [updates] enabled it also checks by itself every few hours.
    updates = UpdateChecker(check_hours=config.updates["check_hours"], config_path=args.config)
    if config.updates["enabled"]:
        updates.start()

    controller = KioskController(
        services.attendance,
        services.users,
        reader=reader,
        site_url=config.tag["site_url"],
        write_tags=config.hardware["nfc"]["write_tags"],
        result_seconds=config.ui["result_seconds"],
        keypad_timeout=config.ui["keypad_timeout_seconds"],
        seasons=services.seasons,
        system=SystemActions(),
        updates=updates,
        # The desktop app (--web-ui) has a desktop menu entry; the Tk window doesn't.
        reopen_hint=("To start it again, restart the Pi, or open NFC Kiosk from the "
                     "desktop menu." if args.web_ui else "To start it again, restart the Pi."),
    )

    # Live "who's here" page on port 8080, in its own thread.
    if not start_in_background(services, config):
        logging.info("live page: off")
    simulated = reader if config.hardware["mode"] == "simulated" else None

    # The Electron app passes --web-ui: serve the screen as a web page instead.
    if args.web_ui:
        run_web_ui(args.web_ui, controller, config, reader, keypad, buzzer, simulated, updates)
        return

    # Otherwise draw the screen with Tkinter (imported here so --web-ui works without Tk).
    import tkinter as tk

    from nfc_login.ui.kiosk_window import KioskWindow
    root = tk.Tk()
    kiosk = KioskWindow(root, controller, config.ui, simulated_reader=simulated, buzzer=buzzer,
                        updates=updates)

    # Background threads: one polls the card reader, one scans the keypad.
    NfcWorker(reader, controller, kiosk.publish).start()
    if keypad is not None:
        KeypadPoller(keypad, kiosk.press,
                     config.hardware["keypad"]["poll_interval_seconds"]).start()
    try:
        root.mainloop()  # runs until the window closes
    finally:
        if keypad is not None:
            keypad.cleanup()  # release the GPIO pins
    if getattr(kiosk, "restart_requested", False):
        restart()


def restart() -> None:
    """Admin menu restart (or an installed update): start again with the code now on disk."""
    logging.info("restarting the kiosk")
    # execv replaces this process, so no second copy fights over the GPIO pins.
    os.execv(sys.executable, [sys.executable, "-m", "nfc_login", *sys.argv[1:]])


def run_web_ui(port, controller, config, reader, keypad, buzzer, simulated, updates) -> None:
    """Same as the Tk path in main(), but the screen is a page on 127.0.0.1:port."""
    from nfc_login.ui.web_kiosk import WebKiosk, serve
    kiosk = WebKiosk(controller, config.ui, buzzer=buzzer, simulated_reader=simulated,
                     updates=updates)
    server = serve(kiosk, port)
    kiosk.on_restart = server.shutdown  # a restart stops serve_forever() below
    NfcWorker(reader, controller, kiosk.publish).start()
    if keypad is not None:
        KeypadPoller(keypad, kiosk.press,
                     config.hardware["keypad"]["poll_interval_seconds"]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if keypad is not None:
            keypad.cleanup()
    if kiosk.restart_requested:
        # Started without the app's control pipe: restart just this program.
        restart()


if __name__ == "__main__":
    main()
