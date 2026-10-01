# Legacy system support

This project can replace the older attendance tracker at
[aesom-e/attendance](https://github.com/aesom-e/attendance) (PHP + MariaDB,
an RC522 RFID reader and a 16x2 LCD). It can:

1. **run on the legacy hardware**: the RC522 reader and the 16x2 LCD, with or
   without the touchscreen and keypad, and
2. **import the legacy database**: users, their cards, current hours, past
   seasons and the visit log.

Old cards keep working: the import converts each stored `rfidKey` back into
the card it came from.

## Importing the legacy database

Run this on the Pi that has the old database. If the new system is on a
different Pi, pass `--host` instead. The import only reads the old database;
it never changes it.

```bash
# See what would happen, without saving anything:
.venv/bin/python -m nfc_login.admin import-legacy \
    --legacy-config /var/www/html/config.json --dry-run

# Then for real:
.venv/bin/python -m nfc_login.admin import-legacy \
    --legacy-config /var/www/html/config.json
```

`--legacy-config` reads the database login from the old `config.json` (its
read-only `php` user). Without it, give `--host`, `--user`, `--password` and
`--database` (default `attendance`).

Options:

| Option | |
|---|---|
| `--sections A` | Put everyone in section A. `--sections A,B` fills A01–A99, then B. Default: all sections, starting at A. |
| `--dry-run` | Print the summary and the new IDs without saving |

It prints a table of old ID → new ID → name, so you can tell people their
new IDs.

### What gets imported

| Legacy | Becomes |
|---|---|
| `users.name`, `userId` | A user with the next free ID in the chosen section. The old `userId` is saved in `users.legacy_id`. |
| `users.rfidKey` | Their card (see below) |
| `users.hours` | An adjustment in the **current** season, reason "Imported from legacy system" |
| `users.lastLogin` / `lastLogout` | Last sign-in / sign-out. People logged in at import time stay signed in, and their time counts when they sign out here. |
| `pastseasons` | One archived season per `seasonStartDate`, named `Legacy 2025-01-06`, with each person's hours. View with `season leaderboard "Legacy 2025-01-06"`. |
| `records` | Copied to the `legacy_records` table for reference. Their time isn't counted twice: it's already in `hours`. |

Running the import again is safe. People already imported (same old
`userId`) are skipped, so a second run picks up only users added to the old
system since.

If a name is already taken here, the person is imported as `Name (old 12)`.
If a card already belongs to someone here, it's skipped with a warning.

### How old cards are matched

The legacy kiosk read cards with the `mfrc522` library's `SimpleMFRC522`,
which stores 5 bytes as one number: the card's 4 UID bytes plus a check byte.

- **4-byte cards** (most RC522 key fobs and white cards): the full UID is in
  the number, so the card is enrolled straight away.
- **7-byte cards** (NTAG stickers and cards): the RC522 only saw `88` plus the
  first 3 UID bytes. The card is stored under its old number and matched on
  its first scan, which also saves its full UID.

Matching by old number also works the other way around. If you keep an RC522
reader, cards enrolled on a PN532 still match.

## Running on the legacy hardware

### RC522 reader

In `config.toml`:

```toml
[hardware.nfc]
reader = "mfrc522"
```

Wiring (SPI0, same as the legacy readme), and enable SPI with
`sudo raspi-config nonint do_spi 0`:

| RC522 | Pi pin |
|---|---|
| SDA | 24 (GPIO8, CE0) |
| SCK | 23 (GPIO11) |
| MOSI | 19 (GPIO10) |
| MISO | 21 (GPIO9) |
| IRQ | not connected |
| GND | 6 |
| RST | 22 (GPIO25) |
| 3.3V | 1 |

Limits compared with the PN532:

- It **doesn't write** the stats and link onto cards. Signing in and out
  works the same.
- 7-byte cards are identified by their first bytes only (as in the legacy
  system).

**The keypad clashes with the RC522**: the Da Vinci Kit wiring uses GPIO10
and GPIO25. Either leave the keypad out:

```toml
[hardware.keypad]
enabled = false
```

or move it to free pins:

```toml
[hardware.keypad]
rows = [5, 6, 12, 13]       # physical pins 29, 31, 32, 33
cols = [16, 26, 20, 21]     # physical pins 36, 37, 38, 40
```

The kiosk refuses to start with a clear message if the pins overlap.

### 16x2 LCD

The I2C LCD (PCF8574 backpack at 0x27, like the legacy one) shows the scan
result on line 1 and the detail or what's being typed on line 2. For the
first minute after start-up it shows the Pi's IP address, like the old
system, so people can find the web page.

| LCD | Pi pin |
|---|---|
| GND | 6 (or 9) |
| VCC | 2 (5V) |
| SDA | 3 (GPIO2) |
| SCL | 5 (GPIO3) |

It shares the I2C bus with the PN532 (different addresses, 0x27 and 0x24).

```toml
[hardware.lcd]
enabled = true      # alongside the touchscreen
address = 0x27      # 0x3F on some backpacks; check with i2cdetect -y 1
```

### Headless mode (no touchscreen)

Like the legacy system: reader + LCD (+ keypad if wired) + the web page.

```toml
[ui]
mode = "headless"
```

or `python3 -m nfc_login --headless`. The LCD is turned on automatically in
this mode. The "who's here" page and admin page at `http://<pi>:8080/`
replace the old PHP site.

### Packages

`requirements-pi.txt` includes `mfrc522`, `spidev` and `smbus2`. The PHP,
Apache and the C programs from the legacy system aren't needed. Once you've
imported, you can stop them:

```bash
sudo crontab -e            # remove the two legacy lines (Scripts/main and cmdLogOutWithoutCredit)
sudo systemctl disable --now apache2
```

The old database stays where it is until you drop it.
