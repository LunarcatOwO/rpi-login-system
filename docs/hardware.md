# Hardware

## Parts

| Part | Used for |
|---|---|
| Raspberry Pi 4 Model B (2 GB+) | Runs the kiosk and MariaDB |
| 5 inch 800x480 touchscreen | Display (the UI is laid out for 800x480) |
| Elechouse PN532 NFC Module V3 | Reads card UIDs and writes card info |
| 4x4 matrix keypad (SunFounder Da Vinci Kit) | Menus, PIN entry |
| 8x 10 kΩ resistors (optional, from the kit) | Pull-downs on the keypad columns |
| NTAG215 cards, fobs or stickers | One per person |
| microSD card (16 GB+), 5 V 3 A USB-C supply | |
| Buzzer (active, 3.3-5 V) | Beeps a different rhythm for each event |
| 3D printed shell | Holds everything |

Coming from the old attendance system? Its MIFARE Classic cards work on the
PN532; see [legacy.md](legacy.md) to import its database.

## Pin map

The keypad, NFC reader and buzzer together use 13 GPIO pins, none shared
(plus CE0, which the SPI driver holds). The PN532 is wired over **SPI**.

```
                 3V3  (1) (2)  5V
                GPIO2  (3) (4)  5V
                GPIO3  (5) (6)  GND ── PN532 GND
                GPIO4 (7) (8)  GPIO14
                  GND (9) (10) GPIO15
 Keypad C4 ─ GPIO17 (11) (12) GPIO18 ─ Keypad R1
 Keypad C3 ─ GPIO27 (13) (14) GND
 Keypad C2 ─ GPIO22 (15) (16) GPIO23 ─ Keypad R2
 PN532 VCC ─    3V3 (17) (18) GPIO24 ─ Keypad R3
PN532 MOSI ─ GPIO10 (19) (20) GND
PN532 MISO ─  GPIO9 (21) (22) GPIO25 ─ Keypad R4
 PN532 SCK ─ GPIO11 (23) (24) GPIO8    (CE0: leave free)
                  GND (25) (26) GPIO7
                GPIO0 (27) (28) GPIO1
  PN532 SS ─  GPIO5 (29) (30) GND
 Keypad C1 ─  GPIO6 (31) (32) GPIO12 ─ Buzzer +  (or transistor base, see below)
               GPIO13 (33) (34) GND    ─ Buzzer −
```

### PN532 NFC Module V3 (SPI)

| PN532 pin | Pi pin |
|---|---|
| VCC | Pin 17 (3.3 V) |
| GND | Pin 6 |
| SCK | Pin 23 (GPIO11) |
| MISO | Pin 21 (GPIO9) |
| MOSI | Pin 19 (GPIO10) |
| SS | Pin 29 (GPIO5) |

Set the two DIP switches on the module to **SPI mode: switch 1 OFF, switch 2
ON**, then turn SPI on (`sudo raspi-config` → Interface Options → SPI) and
reboot. Power it from 3.3 V so its logic matches the Pi's.

SS goes to GPIO5 rather than the Pi's CE0 (GPIO8), which is how Adafruit's
PN532 library expects it; change `spi_cs_pin` if you wire it elsewhere.
`python3 -m nfc_login.admin tag read` checks the wiring: it prints the UID,
the card type and what's written on the card.

#### Why SPI: the three modes compared

The PN532 V3 can talk to the Pi three ways, chosen with the DIP switches.
These are estimates from the protocol speeds and how the Python library
works; none of them has been timed on this Pi yet.

| Mode | Link speed on a Pi 4 | Per command | Notes |
|---|---|---|---|
| **SPI** (used) | 1 MHz here (PN532 allows 5 MHz) | about 3-5 ms | No clock stretching, so no bus errors |
| I2C | 100 kHz by default, 400 kHz max | about 12-20 ms | The PN532 stretches the I2C clock, which the Pi handles badly: the occasional garbled reply |
| HSU (UART) | 115200 baud | about 12-20 ms | Needs Bluetooth moved off the Pi's main UART (`dtoverlay=disable-bt`) |

SPI is the quickest by a clear margin. Each command to the card (read a
block, write a block, unlock a sector) sends about 40 bytes over the wire:
around 0.3 ms over SPI against 4 ms over I2C or UART. On top of that, the
library checks whether the PN532 has finished only every 10 ms. Over SPI
that check is cheap, so the kiosk checks every 1 ms and runs SPI at 1 MHz
instead of the library's 100 kHz (`nfc_reader.py`).

What that means at the kiosk:

- **Signing in** only needs the card's UID, which is one command in any
  mode, so it feels instant either way.
- **Writing the card** (ID, time, rank and link after each scan) is 15 to 50
  commands. That's roughly 0.1-0.25 s over SPI against 0.3-1 s over I2C, and
  a shorter write means less chance of the card being pulled away half way.

I2C and UART still work: set `interface = "i2c"` or `"uart"` in
`[hardware.nfc]`, flip the DIP switches (I2C: 1 ON, 2 OFF; UART: both OFF)
and wire SDA/SCL to pins 3/5, or TX/RX to pins 10/8.

#### When a card isn't read all the way

If a read or write fails part-way, because the card wobbled or there was a
glitch on the wires, the kiosk selects the card again, unlocks its sector
again (MIFARE Classic) and retries only that block. It tries each block up to
4 times (`tries`) and gives up on a card after 4 seconds, so a card that's
been taken away never freezes the kiosk. Reading a card stops as soon as the
card info ends instead of reading the whole card.

### Keypad (Da Vinci Kit lesson 2.1.5)

Same wiring as the kit manual (BCM numbering), except **column 1**. The kit
puts it on GPIO10, which is SPI MOSI and needed by the PN532, so it moves to
**GPIO6 (pin 31)**.

| Keypad | BCM | Physical pin |
|---|---|---|
| Row 1 (1 2 3 A) | GPIO18 | 12 |
| Row 2 (4 5 6 B) | GPIO23 | 16 |
| Row 3 (7 8 9 C) | GPIO24 | 18 |
| Row 4 (* 0 # D) | GPIO25 | 22 |
| Column 1 (1 4 7 *) | **GPIO6** | **31** |
| Column 2 (2 5 8 0) | GPIO22 | 15 |
| Column 3 (3 6 9 #) | GPIO27 | 13 |
| Column 4 (A B C D) | GPIO17 | 11 |

Looking at the keypad face with the ribbon at the bottom, the 8 pins are
usually rows 1-4 then columns 1-4, left to right. If keys come out wrong,
swap the order in `[hardware.keypad]` in `config.toml` rather than rewiring.
The kiosk won't start if a keypad pin is one the PN532 uses.

The kit puts 10 kΩ pull-down resistors on the column lines. The code also
turns on the Pi's internal pull-downs, so the resistors are optional inside
the enclosure; keep them if you get phantom key presses.

### Touchscreen

Use a 5 inch screen that connects by **HDMI + USB (touch)** or **DSI**. Some
5 inch screens instead take their touch signal from the GPIO header over SPI
(an XPT2046 controller on GPIO 7-11 and 25); those clash with the PN532 and
the keypad's row 4. Avoid them, or move keypad row 4 to a free pin (e.g.
GPIO 13, 16, 19, 20, 21) and update `[hardware.keypad]`.

For a portrait or flipped screen, rotate it in Raspberry Pi OS (Screen
Configuration) and the kiosk follows.

### Buzzer

An **active** buzzer (it beeps by itself when powered, like the one in the
Da Vinci Kit) on **GPIO 12, physical pin 32**, with − to **pin 34 (GND)**.

- A small 3.3 V buzzer or a buzzer module can go straight on the pin.
- A bigger 5 V buzzer needs a transistor: the kit's S8050 NPN with a 1 kΩ
  resistor from GPIO 12 to its base, emitter to GND, buzzer between 5 V and
  the collector. (The kit's own buzzer lesson uses GPIO 17, which the keypad
  already uses here, so wire it to GPIO 12 instead.)
- Modules that beep when the pin goes LOW: set `active_low = true`.
- A **passive** buzzer (needs a tone) also works: `type = "passive"`, with
  `frequency` in Hz.

Turn it off with `enabled = false` in `[hardware.buzzer]`, and the keypad
clicks alone with `key_clicks = false`. The kiosk won't start if the pin is
one the keypad or PN532 uses.

Each event has its own rhythm (milliseconds on, off, on...). Hear them with
the WAV files from `python3 scripts/buzzer_samples.py`, or on the real buzzer
with `python -m nfc_login.admin buzzer` (or `buzzer sign_in error`).

| Pattern | Rhythm | When |
|---|---|---|
| `sign_in` | da-da-DAA (70, 50, 70, 50, 180) | Signed in |
| `sign_out` | DAA-da-da (180, 50, 70, 50, 70) | Signed out |
| `success` | da-DAA (70, 50, 180) | Card enrolled, hours saved |
| `ignored` | one blip (60) | Same card tapped again within 10 s |
| `attention` | dit-dit-dit, dit-dit-dit | Old card: pick your team |
| `admin` | seven quick ticks (40 ms each) | Admin menu opened |
| `warning` | two even beeps (150, 120, 150) | Warnings (e.g. no admin PIN set) |
| `error` | two long buzzes (400, 100, 400) | Unknown card, wrong PIN, other errors |
| `key` | click (25) | Every keypad press |

Change any of them in `config.toml`:

```toml
[hardware.buzzer.patterns]
sign_in = [100, 60, 250]
```

## Cards

Use **NTAG215** (504 bytes). NTAG213 (144 bytes) and NTAG216 (888 bytes)
also work; on an NTAG213 a shorter summary is written (see
[nfc-tags.md](nfc-tags.md)).

MIFARE Classic 1K cards (the white cards that come with PN532 kits, and the
legacy system's cards) also work. The card info is written to them too, but
phones can't read it or open the link.

## Enclosure

The 3D printed shell is in the project library under `enclosure/`, in three
parts:

| File | Part | Size (mm) |
|---|---|---|
| `RPI_CHEESE.STL` | Body | 232 x 89 x 130 |
| `RPI_CHEESE_TOP.STL` | Top | 120 x 190 x 5 |
| `RPI_CHEESE_TRAY.STL` | Tray | 82 x 103 x 10 |

This is a work-in-progress version; a newer one will replace these files.
The first single-piece draft is kept in `enclosure/previous/`.

When placing parts:

- Mount the PN532 V3 board (about 43 x 41 mm) flat against the inside of a
  wall with a "tap here" mark outside. Keep the wall 2 mm or thinner in front
  of the antenna and keep metal away from it.
- Leave openings for USB-C power and Ethernet on the Pi side, plus some vent
  slots (the Pi 4 runs warm with the screen on all day).
