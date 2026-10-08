# RPi NFC Login System

A sign-in / sign-out kiosk for a Raspberry Pi 4 B. Tap an NFC card to sign in
and tap again to sign out. Hours are kept in MariaDB and a leaderboard shows on
a 5 inch touchscreen. After each scan the person's stats are written onto their
card, along with a link a phone can open.

![Kiosk after a scan](docs/images/kiosk-scan.png)

How each part works is explained in comments in the code itself, starting from
`nfc_login/__init__.py`.

## Hardware and wiring

Pi 4 B, 5 inch 800x480 HDMI/DSI screen with USB touch, Elechouse PN532 V3 in
SPI mode, the Da Vinci Kit 4x4 keypad, an active buzzer, and NTAG215 cards
(NTAG213/216 and MIFARE Classic 1K work too). Pin numbers below are physical
pin numbers.

| Part | Connections |
|---|---|
| PN532 (DIP switches: 1 OFF, 2 ON) | VCC 17 (3.3 V), GND 6, SCK 23, MISO 21, MOSI 19, SS 29 |
| Keypad rows 1-4 | 12, 16, 18, 22 |
| Keypad columns 1-4 | 31, 15, 13, 11 (column 1 moved off the kit's GPIO 10, which SPI needs) |
| Buzzer | + to 32 (GPIO 12), − to 34 |

No resistors are needed because the code turns on the Pi's own pull-downs.
Pins can be changed in `config.toml`.

## Install on the Pi

On Raspberry Pi OS (64-bit, desktop):

```bash
git clone https://github.com/LunarcatOwO/rpi-login-system.git ~/rpi-login-system && bash ~/rpi-login-system/scripts/setup-pi.sh
```

The script asks for your sudo password and an admin PIN, then reboots. After
that the kiosk opens fullscreen by itself.

To update, use the admin menu (**3 System → 2**) when **⬇ Update** shows by the
clock. You can also update by hand: `cd ~/rpi-login-system && git pull`, then restart the app.

## Using it

| Key | Does |
|---|---|
| A-D | Start typing a team member's ID (A007). Mentors type just the number (007). |
| # | Enter |
| * | Backspace / back. From the start screen it opens the admin menu (asks for the PIN). |

The admin menu has three parts: **1 People** (add, enroll cards, teams, PINs),
**2 Hours** (add/subtract, sign in/out, who's here, new season) and **3 System**
(info, updates, Wi-Fi, restart/shut down). Only admins can enroll cards.

Anyone on the network can open the live "who's here" page at
`http://<pi-address>:8080/`, and the admin page is at `/admin`.
For the command-line admin tool, run `.venv/bin/python -m nfc_login.admin --help`.
Old cards from [aesom-e/attendance](https://github.com/aesom-e/attendance) keep
working. Bring over that system's data with `admin import-legacy --help`.

## Try it without hardware

```bash
pip install -r requirements.txt
cp config.example.toml config.toml   # set the database login
python3 -m nfc_login.admin init-db
python3 -m nfc_login --simulate --windowed
```

The desktop app is in `electron/`. Run it with `npm install && npm run simulate`, or build
the Pi package with `npm run dist:pi`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                     # unit tests
NFC_LOGIN_TEST_DB_USER=root pytest         # plus database tests (needs MariaDB)
```

## License

Created by [LunarcatOwO](https://github.com/LunarcatOwO). Released under the
GNU General Public License v3.0 or later. See [LICENSE](LICENSE).
