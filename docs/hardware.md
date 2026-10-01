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
| 3D printed shell | Holds everything |

## Pin map

The keypad and NFC reader together use 12 GPIO header pins, none shared.

```
                 3V3  (1) (2)  5V
 PN532 SDA ── GPIO2  (3) (4)  5V
 PN532 SCL ── GPIO3  (5) (6)  GND ── PN532 GND
                GPIO4 (7) (8)  GPIO14
                  GND (9) (10) GPIO15
 Keypad C4 ─ GPIO17 (11) (12) GPIO18 ─ Keypad R1
 Keypad C3 ─ GPIO27 (13) (14) GND
 Keypad C2 ─ GPIO22 (15) (16) GPIO23 ─ Keypad R2
                 3V3 (17) (18) GPIO24 ─ Keypad R3
 Keypad C1 ─ GPIO10 (19) (20) GND
                GPIO9 (21) (22) GPIO25 ─ Keypad R4
```

PN532 VCC goes to **pin 1 (3.3 V)**.

### PN532 NFC Module V3 (I2C)

| PN532 pin | Pi pin |
|---|---|
| VCC | Pin 1 (3.3 V) |
| GND | Pin 6 |
| SDA | Pin 3 (GPIO2) |
| SCL | Pin 5 (GPIO3) |

Set the two DIP switches on the module to **I2C mode: switch 1 ON, switch 2 OFF**.

I2C is used rather than SPI because the keypad wiring from the Da Vinci Kit
uses GPIO10, which is the SPI MOSI pin. Power it from 3.3 V so the module's
I2C pull-ups stay at the Pi's 3.3 V logic level.

Check it's detected after enabling I2C (`sudo raspi-config` → Interface
Options → I2C) and rebooting:

```bash
i2cdetect -y 1      # the PN532 shows up at 0x24
```

### Keypad (Da Vinci Kit lesson 2.1.5)

Same wiring as the kit manual, BCM numbering:

| Keypad | BCM | Physical pin |
|---|---|---|
| Row 1 (1 2 3 A) | GPIO18 | 12 |
| Row 2 (4 5 6 B) | GPIO23 | 16 |
| Row 3 (7 8 9 C) | GPIO24 | 18 |
| Row 4 (* 0 # D) | GPIO25 | 22 |
| Column 1 (1 4 7 *) | GPIO10 | 19 |
| Column 2 (2 5 8 0) | GPIO22 | 15 |
| Column 3 (3 6 9 #) | GPIO27 | 13 |
| Column 4 (A B C D) | GPIO17 | 11 |

Looking at the keypad face with the ribbon at the bottom, the 8 pins are
usually rows 1-4 then columns 1-4, left to right. If keys come out wrong,
swap the order in `[hardware.keypad]` in `config.toml` rather than rewiring.

The kit puts 10 kΩ pull-down resistors on the column lines. The code also
turns on the Pi's internal pull-downs, so the resistors are optional inside
the enclosure; keep them if you get phantom key presses.

Leave **SPI disabled** (`raspi-config`), otherwise the SPI driver claims
GPIO10.

### Touchscreen

Use a 5 inch screen that connects by **HDMI + USB (touch)** or **DSI**. Some
5 inch screens instead take their touch signal from the GPIO header over SPI
(an XPT2046 controller on GPIO 7-11 and 25); those clash with the keypad pins.
If yours is one of those, move the keypad to free pins (e.g. GPIO 5, 6, 12,
13, 16, 19, 20, 21) and update `[hardware.keypad]`.

For a portrait or flipped screen, rotate it in Raspberry Pi OS (Screen
Configuration) and the kiosk follows.

## Cards

Use **NTAG215** (504 bytes). NTAG213 (144 bytes) and NTAG216 (888 bytes)
also work; on an NTAG213 a shorter summary is written (see
[nfc-tags.md](nfc-tags.md)).

The white MIFARE Classic cards that often come with PN532 kits still work for
signing in (their UID is read), but nothing is written to them.

## Enclosure

`RPI_CHEESE.STL` in the project library is a simple closed shell, about
**232 x 89 x 130 mm** (128 triangles). Treat it as a starting point; it has
no cut-outs yet. Things to add when modelling the final version:

- Screen window on the front face.
- Keypad opening sized to your keypad (measure it), with its 8 wires routed
  inside.
- The PN532 V3 board (about 43 x 41 mm) mounted flat against the inside of the
  top or front wall with a "tap here" mark outside. Keep the wall 2 mm or
  thinner in front of the antenna and keep metal away from it.
- USB-C power and Ethernet openings on the Pi side, plus some vent slots
  (the Pi 4 runs warm with the screen on all day).
