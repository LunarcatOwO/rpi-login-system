"""Start the kiosk:  python3 -m nfc_login [--config config.toml] [--simulate]"""

from __future__ import annotations

import argparse
import logging
import tkinter as tk

from nfc_login.app import build_services
from nfc_login.config import load_config
from nfc_login.hardware import create_keypad, create_reader
from nfc_login.hardware.keypad import KeypadPoller
from nfc_login.kiosk.controller import KioskController
from nfc_login.kiosk.nfc_worker import NfcWorker
from nfc_login.ui.kiosk_window import KioskWindow


def main() -> None:
    parser = argparse.ArgumentParser(description="NFC sign-in kiosk")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--simulate", action="store_true",
                        help="run without the NFC reader and keypad")
    parser.add_argument("--windowed", action="store_true", help="don't go fullscreen")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    if args.simulate:
        config.hardware["mode"] = "simulated"
    if args.windowed:
        config.ui["fullscreen"] = False

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

    root = tk.Tk()
    simulated = reader if config.hardware["mode"] == "simulated" else None
    window = KioskWindow(root, controller, config.ui, simulated_reader=simulated)

    NfcWorker(reader, controller, window.publish).start()
    if keypad is not None:
        KeypadPoller(keypad, window.press,
                     config.hardware["keypad"]["poll_interval_seconds"]).start()

    try:
        root.mainloop()
    finally:
        if keypad is not None:
            keypad.cleanup()


if __name__ == "__main__":
    main()
