# RPi NFC Login System

A sign-in / sign-out kiosk for a Raspberry Pi 4 B. People tap an NFC card to
sign in and tap again to sign out. The Pi tracks their hours in MariaDB, shows
a live leaderboard on a 5 inch touchscreen, and writes each person's latest
stats back onto their card along with a link a phone can open.

![Kiosk after a scan](docs/images/kiosk-scan.png)

## What it does

- **Tap to sign in, tap again to sign out.** Every scan toggles the person.
- **Hours come from the database only.** The card is identified by its
  factory UID; nothing written on the card is ever read back, so editing a
  card can't add hours.
- **Card contents refresh on every scan:** user ID, username, season time
  (hours + minutes), leaderboard rank, last sign-in, last sign-out, and a link
  to the (placeholder) GitHub Pages site.
- **Leaderboard** on screen, recalculated after every scan.
- **Keypad** (Da Vinci Kit 4x4): admin menu, card enrollment, sign in by ID +
  PIN when someone forgets their card, and "check my time".
- **Season resets** archive the old season, start everyone at 0h 00m for the
  new year, and keep all users, cards and history.
- **Simulator mode** runs the whole kiosk on a normal computer, no hardware.

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi 4 Model B | Raspberry Pi OS Bookworm (desktop) |
| 5 inch 800x480 touchscreen | HDMI/DSI, USB touch |
| Elechouse PN532 NFC Module V3 | I2C mode |
| 4x4 matrix keypad | From the SunFounder Da Vinci Kit |
| NTAG215 cards/stickers | Recommended; NTAG213/216 also work |
| 3D printed shell | `RPI_CHEESE.STL` in the project library |

Wiring is in [docs/hardware.md](docs/hardware.md).

## Quick start (on the Pi)

```bash
git clone https://github.com/LunarcatOwO/rpi-login-system.git
cd rpi-login-system
bash scripts/install.sh                         # packages, MariaDB, I2C, service
.venv/bin/python -m nfc_login.admin set-admin-pin
.venv/bin/python -m nfc_login.admin user add "Taylor"
sudo reboot
```

Then enroll a card from the kiosk: press **A**, enter the admin PIN and **#**,
press **1**, type the user ID and **#**, and tap the new card.

## Try it without hardware

```bash
pip install -r requirements.txt
cp config.example.toml config.toml   # set the database login, mode = "simulated"
python3 -m nfc_login.admin init-db
python3 -m nfc_login.admin user add "Test User"
python3 -m nfc_login.admin tag enroll 1 --uid 04A1B2C3D4E5F6
python3 -m nfc_login --simulate --windowed
```

## Documentation

| Doc | What's in it |
|---|---|
| [docs/hardware.md](docs/hardware.md) | Parts, wiring, PN532 switch settings, the enclosure |
| [docs/setup.md](docs/setup.md) | Installing on the Pi step by step, autostart, backups |
| [docs/usage.md](docs/usage.md) | Scanning, keypad menus, the admin command reference |
| [docs/nfc-tags.md](docs/nfc-tags.md) | What's written on each card and why it's never trusted |
| [docs/seasons.md](docs/seasons.md) | How season resets and archives work |
| [docs/database.md](docs/database.md) | MariaDB tables and how hours are calculated |
| [docs/architecture.md](docs/architecture.md) | How the code is organised |

## Code layout

```
nfc_login/
  config.py      settings from config.toml
  db/            MariaDB schema, connection, SQL queries
  hardware/      PN532 reader, keypad, simulator
  tags/          NDEF encoding and the card payload
  services/      attendance, leaderboard, seasons, users, PINs
  kiosk/         scan + keypad behaviour, NFC polling thread
  ui/            Tkinter touchscreen window
  admin/         command-line admin tool
tests/           pytest suite
scripts/         install script and systemd unit
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                     # unit tests only
NFC_LOGIN_TEST_DB_USER=root pytest         # plus database tests (needs MariaDB)
```

The database tests create and drop a `nfc_login_test` database.
