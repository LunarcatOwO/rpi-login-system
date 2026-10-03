#!/usr/bin/env bash
# Set up a fresh Raspberry Pi as the sign-in kiosk in one go. Run it from the
# repo folder, as your normal user:
#
#   bash scripts/setup-pi.sh
#
# It runs scripts/install.sh (packages, SPI, database, Python), asks for the
# admin PIN if none is set, installs the desktop app (electron/) and makes it
# open fullscreen at boot, turns off screen blanking, and offers to reboot.
# Safe to run again: each step skips or repeats harmlessly.
#
# Pass a downloaded nfc-kiosk-*-arm64.deb to install that instead of fetching
# Electron:  bash scripts/setup-pi.sh path/to/nfc-kiosk-0.2.0-arm64.deb
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user; it calls sudo itself where needed." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ELECTRON_VERSION="44.5.1"          # keep in step with electron/package-lock.json
APP_DIR="$HOME/.local/share/nfc-kiosk"
DEB="${1:-}"
cd "$REPO_DIR"

echo "==> 1/5 Packages, SPI, database and Python (scripts/install.sh)"
bash "$REPO_DIR/scripts/install.sh"

echo "==> 2/5 Admin PIN"
if .venv/bin/python - <<'PY'
import sys
from nfc_login.app import build_services
from nfc_login.config import load_config
sys.exit(0 if build_services(load_config("config.toml")).users.admin_pin_set() else 1)
PY
then
    echo "Already set (change it with: .venv/bin/python -m nfc_login.admin set-admin-pin)"
else
    .venv/bin/python -m nfc_login.admin set-admin-pin
fi

echo "==> 3/5 Desktop app"
if [ -n "$DEB" ]; then
    bash "$REPO_DIR/electron/install-pi.sh" "$DEB"
else
    # Electron's own prebuilt release, checked against its published checksums.
    sudo apt-get install -y unzip curl libnss3
    ZIP="electron-v$ELECTRON_VERSION-linux-arm64.zip"
    URL="https://github.com/electron/electron/releases/download/v$ELECTRON_VERSION"
    if [ ! -x "$APP_DIR/electron-v$ELECTRON_VERSION/electron" ]; then
        TMP="$(mktemp -d)"
        curl -fL --retry 3 -o "$TMP/$ZIP" "$URL/$ZIP"
        curl -fsSL --retry 3 -o "$TMP/SHASUMS256.txt" "$URL/SHASUMS256.txt"
        (cd "$TMP" && grep " \*$ZIP\$" SHASUMS256.txt | sha256sum -c -)
        mkdir -p "$APP_DIR"
        unzip -q -o "$TMP/$ZIP" -d "$APP_DIR/electron-v$ELECTRON_VERSION"
        rm -rf "$TMP"
    fi
    # Chromium's sandbox helper must be owned by root (the .deb does the same).
    sudo chown root:root "$APP_DIR/electron-v$ELECTRON_VERSION/chrome-sandbox"
    sudo chmod 4755 "$APP_DIR/electron-v$ELECTRON_VERSION/chrome-sandbox"
    ln -sfn "$APP_DIR/electron-v$ELECTRON_VERSION" "$APP_DIR/electron"

    # The app starts the kiosk itself; two programs can't share the reader.
    sudo systemctl disable --now nfc-login.service 2>/dev/null || true

    # Starts at login, and is in the desktop menu to reopen it after
    # closing it (admin menu: 3 System, 6 on the keypad).
    mkdir -p "$HOME/.config/autostart" "$HOME/.local/share/applications"
    cat > "$HOME/.local/share/applications/nfc-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=NFC Kiosk
Comment=NFC sign-in kiosk
Exec=$APP_DIR/electron/electron $REPO_DIR/electron --home $REPO_DIR
Terminal=false
Categories=Utility;
X-GNOME-Autostart-enabled=true
EOF
    cp "$HOME/.local/share/applications/nfc-kiosk.desktop" "$HOME/.config/autostart/"
fi

echo "==> 4/5 Boot straight to the desktop, screen always on"
sudo raspi-config nonint do_boot_behaviour B4    # desktop, logged in automatically
sudo raspi-config nonint do_blanking 1           # 1 = screen blanking off

# The kiosk shows a reminder until this has run after an update that changed setup.
rm -f "$REPO_DIR/.setup-needed"
echo "==> 5/5 Done"
echo "After a reboot the kiosk opens fullscreen by itself. To close it: admin menu 3, then 6"
echo "on the keypad (or Ctrl+Alt+Q). Reopen it from the desktop menu: NFC Kiosk."
echo "Add people on the kiosk: press *, the admin PIN, #, then 1 People, 1 Add a user."
read -r -p "Reboot now? [Y/n] " answer
case "$answer" in
    [nN]*) echo "Reboot when you're ready: sudo reboot" ;;
    *) sudo reboot ;;
esac
