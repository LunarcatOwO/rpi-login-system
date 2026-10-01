# How the code is organised

```
nfc_login/
├── __main__.py         python -m nfc_login: builds everything and starts the kiosk
├── app.py              build_services(): one place that wires DB + services
├── config.py           config.toml loading, merged over defaults
├── db/
│   ├── schema.sql      tables
│   ├── connection.py   Database: connections, transactions, schema apply
│   └── repository.py   every SQL query, as small functions
├── hardware/
│   ├── nfc_reader.py   PN532 over I2C: read UID, write NDEF to NTAG
│   ├── keypad.py       Da Vinci Kit 4x4 keypad scanner + polling thread
│   ├── mfrc522_reader.py  legacy RC522 reader (SPI)
│   ├── lcd1602.py      legacy 16x2 I2C LCD
│   └── simulated.py    fake reader for running on a PC
├── legacy/
│   ├── rfid.py         legacy card numbers <-> UIDs
│   └── importer.py     import from the legacy attendance database
├── tags/
│   ├── ndef.py         NDEF record/TLV encoding (no extra dependencies)
│   └── payload.py      what goes on a card, shrunk to fit small tags
├── services/
│   ├── attendance.py   sign in/out toggle, stats, sign-out-all, stale clean-up
│   ├── leaderboard.py  ranking
│   ├── seasons.py      season reset + CSV archive
│   ├── users.py        users, section IDs, cards, admin PIN
│   ├── ids.py          A07-style user IDs
│   ├── pins.py         PIN hashing
│   └── timefmt.py      "12h 34m" and timestamp formatting
├── kiosk/
│   ├── controller.py   what a scan or key press does; returns a Screen
│   ├── nfc_worker.py   thread: poll reader → controller → screen
│   └── headless.py     no-touchscreen mode (LCD + keypad + web)
├── ui/
│   ├── kiosk_window.py Tkinter window for 800x480 (here-now + leaderboard tabs)
│   └── lcd_output.py   screens on the 16x2 LCD
├── web/
│   ├── server.py       live page, JSON API, admin page (stdlib http.server)
│   └── live.html       the live "who's here" page (polls /api/status)
└── admin/
    └── cli.py          python -m nfc_login.admin
```

Each layer only talks to the one below it:

```
ui / web page / admin CLI
      │
kiosk controller ── hardware (reader, keypad)
      │                  │
  services ───────────── tags (payload, ndef)
      │
  db (repository → MariaDB)
```

## A card scan, step by step

1. `NfcWorker` polls `reader.read_uid()`. A card left on the reader is only
   handled once.
2. `KioskController.handle_card(uid)` calls `AttendanceService.scan_tag(uid)`.
3. `scan_tag` looks up the UID in `tags`, then `toggle()`:
   - locks the user row (`SELECT … FOR UPDATE`),
   - signs out if a session is open (credit = now − sign-in), otherwise signs in,
   - ignores repeats within `min_scan_interval_seconds`.
4. Fresh `UserStats` are calculated: season total, rank among all users,
   last sign-in/out.
5. The controller builds the NDEF message (`tags.payload.build_message`) and
   writes it to the card still on the reader.
6. A `Screen` goes back to the UI thread through a queue; the leaderboard
   refreshes.

## Threads

- **Tk main thread:** draws the screen, drains the event queue every 50 ms.
- **nfc:** polls the reader, runs scans.
- **keypad:** scans the keypad matrix every 50 ms (real hardware only).
- **keys:** handles key presses in order, so PIN hashing and DB calls never
  freeze the screen.
- **web:** the HTTP server for the live page and admin page (one short thread
  per request).

The controller holds a lock, so a card scan and a key press never run at the
same time.

## Testing

`tests/` covers NDEF encoding, card payload sizing, ranking, PIN hashing,
config loading, user IDs, sign-in/out rules, hour adjustments, season resets,
the keypad menus, the web page (live data, admin login, adjustments) and the
legacy import (run against the legacy system's own schema). The
database tests run against a real MariaDB (see the README); the controller
tests use the simulated reader.
