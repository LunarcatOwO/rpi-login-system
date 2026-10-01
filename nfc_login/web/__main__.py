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
server.serve_forever()
