"""Start the kiosk:  python3 -m nfc_login [--config config.toml] [--simulate]

With --web-ui the screen is served as a local web page instead of a Tkinter
window; the Electron app (electron/) starts the kiosk this way and shows it.
"""

from __future__ import annotations

import argparse
import logging

from nfc_login.app import build_services
from nfc_login.config import load_config
from nfc_login.hardware import create_buzzer, create_keypad, create_reader
from nfc_login.hardware.keypad import KeypadPoller
from nfc_login.kiosk.controller import KioskController
from nfc_login.kiosk.nfc_worker import NfcWorker
from nfc_login.web.server import start_in_background


def main() -> None:
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
    if args.simulate:
        config.hardware["mode"] = "simulated"
    if args.windowed:
        config.ui["fullscreen"] = False

    services = build_services(config)
    season = services.seasons.ensure_active()
    logging.info("active season: %s", season["name"])

    reader = create_reader(config)
    keypad = create_keypad(config)
    buzzer = create_buzzer(config)
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

    start_in_background(services, config)
    simulated = reader if config.hardware["mode"] == "simulated" else None

    if args.web_ui:
        run_web_ui(args.web_ui, controller, config, reader, keypad, buzzer, simulated)
        return

    import tkinter as tk

    from nfc_login.ui.kiosk_window import KioskWindow
    root = tk.Tk()
    kiosk = KioskWindow(root, controller, config.ui, simulated_reader=simulated, buzzer=buzzer)

    NfcWorker(reader, controller, kiosk.publish).start()
    if keypad is not None:
        KeypadPoller(keypad, kiosk.press,
                     config.hardware["keypad"]["poll_interval_seconds"]).start()
    try:
        root.mainloop()
    finally:
        if keypad is not None:
            keypad.cleanup()


def run_web_ui(port, controller, config, reader, keypad, buzzer, simulated) -> None:
    from nfc_login.ui.web_kiosk import WebKiosk, serve
    kiosk = WebKiosk(controller, config.ui, buzzer=buzzer, simulated_reader=simulated)
    server = serve(kiosk, port)
    NfcWorker(reader, controller, kiosk.publish).start()
    if keypad is not None:
        KeypadPoller(keypad, kiosk.press,
                     config.hardware["keypad"]["poll_interval_seconds"]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if keypad is not None:
            keypad.cleanup()


if __name__ == "__main__":
    main()
