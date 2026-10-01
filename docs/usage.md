# Using the kiosk

## Signing in and out

Hold your card on the reader until the screen changes.

- **Signed out → signed in:** "Welcome, *name*!" with your season total and rank.
- **Signed in → signed out:** "Goodbye, *name*!" with this session's length,
  your new total and rank.

Keep the card on the reader for a moment after the screen changes: that's
when your latest stats are written onto it.

Rules:

- Scanning again within **10 seconds** of signing in or out is ignored, so a
  double tap doesn't undo itself. (`min_scan_interval_seconds`)
- If you forget to sign out, the session is closed with **no time credited**
  once it passes **12 hours** (at your next scan, or by the 03:00 clean-up).
  (`max_session_hours`)
- Unknown or removed cards show "Card not registered".

## Keypad

```
 1  2  3  A        A  admin menu
 4  5  6  B        B  sign in/out without a card (ID + PIN)
 7  8  9  C        C  check my time
 *  0  #  D        D  cancel
```

`#` is Enter. `*` deletes the last digit, or goes back a step when nothing is
typed. Half-typed input clears itself after 30 seconds.

### B: sign in/out without a card

`B`, your user ID, `#`, your PIN, `#`. Only works for users who have a PIN
(set with `user set-pin`). Five wrong PINs lock the keypad for a minute.

### C: check my time

`C`, your user ID, `#`. Shows season total, rank, whether you're signed in,
and your last sign-in and sign-out.

### A: admin menu

`A`, the admin PIN, `#`, then:

| Key | Action |
|---|---|
| 1 | Enroll a card: type the user ID, `#`, then tap the new card |
| 2 | Sign everyone out (end of the day; time is credited) |
| 3 | Show who is signed in |
| 4 | System info (hostname, IP address, reader firmware) |
| D | Exit |

## Admin command line

Run on the Pi from the repo folder (over SSH is fine):

```bash
.venv/bin/python -m nfc_login.admin <command>
```

| Command | What it does |
|---|---|
| `init-db` | Create tables and the first season. Safe to re-run. |
| `set-admin-pin` | Set the kiosk admin PIN |
| `user add "Name" [--pin]` | Create a user (prints the new ID) |
| `user list [--all]` | List users |
| `user show ID` | Time, rank, last sign-in/out |
| `user rename ID "New Name"` | Change a username |
| `user set-pin ID [--clear]` | Set or remove a keypad PIN |
| `user deactivate ID` / `activate ID` | Hide someone (history is kept; their cards stop working) |
| `tag enroll ID [--uid HEX]` | Link a card. Without `--uid` it waits for a tap (stop the kiosk first) |
| `tag list` | All cards and owners |
| `tag remove UID` | Lost card: stop it working |
| `tag read` | Print the UID of the card on the reader |
| `season show` / `season list` | Current / all seasons |
| `season new [NAME] [--yes]` | Season reset (see [seasons.md](seasons.md)) |
| `season leaderboard [NAME]` | Leaderboard for any season |
| `season export [NAME]` | Write CSV files for a season |
| `sessions open` | Who's signed in now |
| `sessions sign-out-all` | Sign everyone out, crediting time |
| `sessions close-stale` | Close sessions older than `max_session_hours` with no credit |

Typical first day:

```bash
python -m nfc_login.admin set-admin-pin
python -m nfc_login.admin user add "Taylor"       # -> ID 1
python -m nfc_login.admin user add "Alex"       # -> ID 2
# then on the kiosk: A, PIN, #, 1, 1, #, tap Taylor's card ... and so on
```

A lost card: `tag remove <old UID>` (see `tag list`), then enroll a new one.

## Simulator

`python3 -m nfc_login --simulate --windowed` (or `mode = "simulated"` in
`config.toml`) runs on any computer with Python 3.11+, Tk and access to a
MariaDB server. A yellow bar at the bottom lets you type a card UID and press
**Tap card**. The computer keyboard acts as the keypad: `0-9`, `A-D`, `*`,
`#`, with Enter = `#`, Backspace = `*`, Esc = `D`.
