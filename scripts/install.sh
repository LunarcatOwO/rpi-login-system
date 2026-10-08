#!/usr/bin/env bash
# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

# One-time setup on Raspberry Pi OS (Bookworm). Run from the repo folder:
#   bash scripts/install.sh
# Most people want scripts/setup-pi.sh instead, which runs this and the rest.
set -euo pipefail   # stop at the first error or unset variable

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user; it calls sudo itself where needed." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # the folder above scripts/
APP_USER="$USER"
DB_NAME="nfc_login"
DB_USER="nfc_login"

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-tk python3-dev mariadb-server

echo "==> Enabling SPI (for the PN532 reader)"
sudo raspi-config nonint do_spi 0

echo "==> Python virtual environment"
# --system-site-packages: the venv can also use apt's packages (RPi.GPIO, tkinter).
python3 -m venv --system-site-packages "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements-pi.txt"

if [ ! -f "$REPO_DIR/config.toml" ]; then   # first run only
    echo "==> Creating MariaDB database and user"
    # A random password, written only into config.toml.
    DB_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
    sudo mariadb <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL
    # config.toml = the example with the new password filled in.
    sed "s/^password = .*/password = \"$DB_PASS\"/" "$REPO_DIR/config.example.toml" \
        > "$REPO_DIR/config.toml"
    chmod 600 "$REPO_DIR/config.toml"   # only this user can read the password
fi

echo "==> Creating tables and the first season"
cd "$REPO_DIR"
"$REPO_DIR/.venv/bin/python" -m nfc_login.admin init-db

echo "==> Installing the kiosk service"
# The unit file is written for user "pi"; swap in this user and folder.
sed -e "s#/home/pi/rpi-login-system#$REPO_DIR#g" \
    -e "s#^User=pi#User=$APP_USER#" \
    -e "s#/home/pi/.Xauthority#$HOME/.Xauthority#" \
    "$REPO_DIR/scripts/nfc-login.service" | sudo tee /etc/systemd/system/nfc-login.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable nfc-login.service

echo "==> Nightly clean-up of forgotten sign-outs (03:00)"
CRON_LINE="0 3 * * * cd $REPO_DIR && $REPO_DIR/.venv/bin/python -m nfc_login.admin sessions close-stale"
# Replace any old copy of the line, keep the rest of the crontab.
( crontab -l 2>/dev/null | grep -v 'nfc_login.admin sessions close-stale' || true
  echo "$CRON_LINE" ) | crontab -

echo
echo "Done. Next steps:"
echo "  1. Set the admin PIN:   .venv/bin/python -m nfc_login.admin set-admin-pin"
echo "  2. Add users:           .venv/bin/python -m nfc_login.admin user add \"Name\" --section A"
echo "  3. Reboot (SPI needs it the first time), then the kiosk starts on its own."
