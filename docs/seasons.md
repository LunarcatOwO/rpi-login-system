# Seasons and resets

A **season** is one tracking period, normally a school or competition year.
Exactly one season is active. Hours, the leaderboard and rank are all
calculated for the active season only.

The first season is created by `init-db` and named after the current year.

## Season reset

```bash
python -m nfc_login.admin season new            # named after the current year
python -m nfc_login.admin season new 2027-2028  # or any name you like
```

It asks you to type `YES` (skip with `--yes`). Then, in one database
transaction:

1. Everyone still signed in is signed out. Their time up to now goes to the
   **old** season (unless they'd been in longer than `max_session_hours`).
2. The old season is marked ended (`ended_at`, `is_active = 0`).
3. The new season is created and made active.

After that it writes two CSV snapshots of the old season to the archive
folder (`[seasons] archive_dir`, default `archive/`):

- `<season>-leaderboard.csv`: rank, user ID, username, total seconds, hours, minutes
- `<season>-sessions.csv`: every sign-in and sign-out

## What is kept

| | After a reset |
|---|---|
| Users, IDs, usernames, PINs | Kept |
| Cards | Kept, keep working |
| Old season's sessions | Kept in the database, linked to the old season |
| Hours shown on the kiosk | Start at 0h 00m for everyone |
| Last sign-in / sign-out | Kept (they're facts about the person, not the season) |

Nothing is deleted, so old seasons can still be viewed:

```bash
python -m nfc_login.admin season list
python -m nfc_login.admin season leaderboard 2026
python -m nfc_login.admin season export 2026
```

Cards show the new season's numbers the next time each person taps in.

Season names must be unique; `season new 2026` fails if `2026` already exists.
