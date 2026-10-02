#!/usr/bin/env bash
# One-time setup on Raspberry Pi OS (Bookworm). Run from the repo folder:
#   bash scripts/install.sh
# See docs/setup.md for what each step does.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user; it calls sudo itself where needed." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$USER"
DB_NAME="nfc_login"
DB_USER="nfc_login"

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-tk python3-dev mariadb-server

echo "==> Enabling SPI (for the PN532 reader)"
sudo raspi-config nonint do_spi 0

echo "==> Python virtual environment"
python3 -m venv --system-site-packages "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements-pi.txt"

if [ ! -f "$REPO_DIR/config.toml" ]; then
    echo "==> Creating MariaDB database and user"
    DB_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
    sudo mariadb <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL
    sed "s/^password = .*/password = \"$DB_PASS\"/" "$REPO_DIR/config.example.toml" \
        > "$REPO_DIR/config.toml"
    chmod 600 "$REPO_DIR/config.toml"
fi

echo "==> Creating tables and the first season"
cd "$REPO_DIR"
"$REPO_DIR/.venv/bin/python" -m nfc_login.admin init-db

echo "==> Installing the kiosk service"
sed -e "s#/home/pi/rpi-login-system#$REPO_DIR#g" \
    -e "s#^User=pi#User=$APP_USER#" \
    -e "s#/home/pi/.Xauthority#$HOME/.Xauthority#" \
    "$REPO_DIR/scripts/nfc-login.service" | sudo tee /etc/systemd/system/nfc-login.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable nfc-login.service

echo "==> Nightly clean-up of forgotten sign-outs (03:00)"
CRON_LINE="0 3 * * * cd $REPO_DIR && $REPO_DIR/.venv/bin/python -m nfc_login.admin sessions close-stale"
( crontab -l 2>/dev/null | grep -v 'nfc_login.admin sessions close-stale' || true
  echo "$CRON_LINE" ) | crontab -

echo
echo "Done. Next steps:"
echo "  1. Set the admin PIN:   .venv/bin/python -m nfc_login.admin set-admin-pin"
echo "  2. Add users:           .venv/bin/python -m nfc_login.admin user add \"Name\" --section A"
echo "  3. Reboot (SPI needs it the first time), then the kiosk starts on its own."
