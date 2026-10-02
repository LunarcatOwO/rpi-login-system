# Legacy system support

This project can replace the older attendance tracker at
[aesom-e/attendance](https://github.com/aesom-e/attendance) (PHP + MariaDB
with an RC522 reader). It doesn't run the old system or its hardware. It
**imports the old database** (users, their cards, current hours, past seasons
and the visit log), and **the old cards keep working** on the PN532: the
import converts each stored `rfidKey` back into the card it came from.

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
| `--sections A` | Put everyone straight onto team A instead of them choosing at their first scan. `--sections A,B` fills A001–A999, then B. |
| `--dry-run` | Print the summary and the new IDs without saving |

It prints a table of old ID → new ID → name.

### Old cards pick their team on the first scan

The old system only knew each card, not which team its owner is on. So by
default imported people get a temporary **U** ID (U001, U002...), meaning "no
team yet". The first time someone taps their old card, the kiosk beeps
dit-dit-dit and asks them to pick:

```
Welcome, Alex!
Please choose your team before signing in.
1  Robot
2  Impact
3  Sustainability
4  Strategy
5  Mentors  (needs an admin)
*  cancel (you won't be signed in)
```

Pressing 1-4 gives them the next free ID on that team (e.g. C004), shows it
on screen, and signs them in or out as usual. Mentors need the admin PIN
first; `*` there goes back to the list. Once the team is set it never asks
again. `*`, a wrong admin PIN or walking away leaves them unsigned and asks
again next time.

An admin can also place someone without a scan: admin menu → 6 on the kiosk,
"Change someone's team" on the web admin page, or
`python -m nfc_login.admin user move U003 B` (`M` for mentors). U can't be
used as a team letter in `config.toml`.

### What gets imported

| Legacy | Becomes |
|---|---|
| `users.name`, `userId` | A user with a U ID until they pick their team (or the next free ID in `--sections`). The old `userId` is saved in `users.legacy_id`. |
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

### Old cards on the PN532

The legacy cards are **MIFARE Classic 1K** (4-byte UID, SAK 08). The PN532
reads them the same way the RC522 did, and the kiosk also writes the card info
onto them (ID, time, rank, last in/out and the link) in sectors 1-15, using
the factory key.

**Phones can't read the info or open the link from these cards**: most phones
don't support MIFARE Classic. Signing in and out works exactly the same. Give
people an NTAG215 card instead if they want the phone link.

`tag read` on the admin command line prints the card type.

## After importing

The PHP site, Apache and the C programs from the legacy system aren't needed.
Once you've imported, you can stop them:

```bash
sudo crontab -e            # remove the two legacy lines (Scripts/main and cmdLogOutWithoutCredit)
sudo systemctl disable --now apache2
```

The old database stays where it is until you drop it. The "who's here" page
and admin page at `http://<pi>:8080/` replace the old PHP site.
