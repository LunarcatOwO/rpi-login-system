"""Start the kiosk:  python3 -m nfc_login [--config config.toml] [--simulate]"""

from __future__ import annotations

import argparse
import logging

from nfc_login.app import build_services
from nfc_login.config import load_config
from nfc_login.hardware import create_keypad, create_lcd, create_reader
from nfc_login.hardware.keypad import KeypadPoller
from nfc_login.kiosk.controller import KioskController
from nfc_login.kiosk.nfc_worker import NfcWorker
from nfc_login.ui.lcd_output import LcdOutput
from nfc_login.web.server import start_in_background


def main() -> None:
    parser = argparse.ArgumentParser(description="NFC sign-in kiosk")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--simulate", action="store_true",
                        help="run without the NFC reader and keypad")
    parser.add_argument("--windowed", action="store_true", help="don't go fullscreen")
    parser.add_argument("--headless", action="store_true",
                        help="no touchscreen: LCD + keypad + web page (legacy setup)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    if args.simulate:
        config.hardware["mode"] = "simulated"
    if args.windowed:
        config.ui["fullscreen"] = False
    if args.headless:
        config.ui["mode"] = "headless"

    services = build_services(config)
    season = services.seasons.ensure_active()
    logging.info("active season: %s", season["name"])

    reader = create_reader(config)
    keypad = create_keypad(config)
    logging.info("NFC reader: %s", reader.firmware_version())

    controller = KioskController(
        services.attendance,
        services.users,
        reader=reader,
        site_url=config.tag["site_url"],
        write_tags=config.hardware["nfc"]["write_tags"],
        result_seconds=config.ui["result_seconds"],
        keypad_timeout=config.ui["keypad_timeout_seconds"],
    )

    lcd = create_lcd(config)
    mirrors = [LcdOutput(lcd, controller)] if lcd else []
    start_in_background(services, config)

    if config.ui["mode"] == "headless":
        from nfc_login.kiosk.headless import HeadlessKiosk
        kiosk = HeadlessKiosk(controller, mirrors)
        run = kiosk.run_forever
    else:
        import tkinter as tk

        from nfc_login.ui.kiosk_window import KioskWindow
        root = tk.Tk()
        simulated = reader if config.hardware["mode"] == "simulated" else None
        kiosk = KioskWindow(root, controller, config.ui, simulated_reader=simulated,
                            mirrors=mirrors)
        run = root.mainloop

    NfcWorker(reader, controller, kiosk.publish).start()
    if keypad is not None:
        KeypadPoller(keypad, kiosk.press,
                     config.hardware["keypad"]["poll_interval_seconds"]).start()
    try:
        run()
    finally:
        if keypad is not None:
            keypad.cleanup()


if __name__ == "__main__":
    main()
