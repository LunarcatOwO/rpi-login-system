# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Run only the web page:  python3 -m nfc_login.web [--config config.toml]"""

import argparse
import logging

from nfc_login.app import build_services
from nfc_login.config import load_config
from nfc_login.web.server import create_server

parser = argparse.ArgumentParser(description="NFC login live page + admin page")
parser.add_argument("--config", help="path to config.toml")
args = parser.parse_args()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

config = load_config(args.config)
web = config.web
server = create_server(build_services(config), config.sections, web["host"], web["port"],
                       web["refresh_seconds"])
logging.info("serving on http://%s:%s/", web["host"], web["port"])
server.serve_forever()  # until Ctrl+C
