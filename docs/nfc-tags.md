# NFC cards

## How a card identifies a person

Every NFC tag has a **UID** burned in at the factory (7 bytes on NTAG, shown
as hex like `04A1B2C3D4E5F6`). The `tags` table links a UID to a user. On a
scan the kiosk reads only the UID, looks it up, and does everything else in
MariaDB.

**Nothing written on the card is ever read back.** The stats on the card are a
copy for people to look at. Someone can rewrite their card with a phone app
and change the "Time" line, and it makes no difference: hours are always
`SUM(credited_seconds)` from the database (see [database.md](database.md)).

Limitation: special "magic" cards that let you change the UID exist. If that
matters, add a keypad PIN check for card scans, or use NTAG 424 DNA cards with
cryptographic authentication (not supported yet).

## What's written on the card

After every sign-in, sign-out and enrollment, the kiosk writes an NDEF
message with two records:

**1. Link (URI record)** — first, so a phone that taps the card opens it:

```
https://lunarcatowo.github.io/rpi-login-system/?id=B12
```

This is a **placeholder** for a future GitHub Pages site that can read the
card with Web NFC. Change `[tag] site_url` in `config.toml` when the site
exists. The site should show the card's text but, like the kiosk, should not
treat it as authoritative.

**2. Info (Text record, English, UTF-8):**

```
ID: B12
User: taylor
Season: 2026
Time: 12h 34m
Rank: #3 of 25
Last in: 2026-10-01 16:30
Last out: 2026-10-01 18:05
```

The rank is recalculated on every scan, so it's current as of the last time
that card was tapped.

## Card sizes

| Tag | NDEF space | What fits |
|---|---|---|
| NTAG213 | 144 bytes | Link + one-line summary: `B12\|taylor\|12h34m\|#3\|in 10-01 16:30\|out 10-01 18:05` |
| NTAG215 | 496 bytes | Everything (recommended) |
| NTAG216 | 872 bytes | Everything |
| MIFARE Classic 1K | – | Signs in by UID; nothing written |

The kiosk reads the tag's capability container to find its size and writes
the largest version that fits. A long `site_url` uses up NTAG213 space fast.

## Writing failures

If the card is pulled away mid-write, the sign-in/out still counts (it's
already in the database) and the screen says "Card info not updated". The
next scan rewrites it.

Cards are never locked (made read-only), so they can always be rewritten.

## Formatting new cards

NTAG cards ship NDEF-formatted, so they work straight away. If a card says it
"can't store info", it has probably been locked or isn't an NTAG; it still
works for signing in.
