# Database (MariaDB)

The schema is in [`nfc_login/db/schema.sql`](../nfc_login/db/schema.sql) and
is applied by `python -m nfc_login.admin init-db`. Every statement is
`CREATE TABLE IF NOT EXISTS`, so re-running it is safe.

## Tables

```
users ──< tags            a user can have several cards (e.g. a replacement)
users ──< sessions >── seasons
users ──< adjustments >── seasons   admin corrections (+/- time)
settings                  key/value (admin PIN hash)
```

### `users`
| Column | |
|---|---|
| `id` | Internal key, never shown |
| `section`, `number` | The user ID people see is these two together: `A` + `7` → **A07**. Unique as a pair. |
| `username` | Unique |
| `pin_hash` | Optional keypad PIN, PBKDF2-SHA256 |
| `is_active` | 0 hides the user and disables their cards |
| `created_at` | |

### `tags`
| Column | |
|---|---|
| `uid` | Card's factory UID, upper-case hex (primary key) |
| `user_id` | Owner |
| `enrolled_at` | |
| `is_active` | 0 after `tag remove` |

### `seasons`
| Column | |
|---|---|
| `id`, `name` | Name is unique, e.g. `2026` |
| `started_at`, `ended_at` | `ended_at` is NULL for the active season |
| `is_active` | Exactly one row is 1 |

### `sessions`
One row per sign-in.

| Column | |
|---|---|
| `user_id`, `season_id` | |
| `sign_in_at`, `sign_in_method` | method: `card` or `keypad` |
| `sign_out_at`, `sign_out_method` | NULL while signed in. Method: `card`, `keypad`, `timeout`, `season_reset`, `admin` |
| `credited_seconds` | Set at sign-out from the two timestamps; 0 for `timeout` |

### `adjustments`
Hours an admin added or took away.

| Column | |
|---|---|
| `user_id`, `season_id` | Adjustments belong to the season they were made in |
| `seconds` | Positive adds time, negative subtracts |
| `reason` | Free text |
| `created_at`, `created_via` | `kiosk`, `web` or `cli` |

Adjustments are never edited or deleted; to undo one, add the opposite.

## How hours are calculated

At sign-out, `credited_seconds = sign_out_at - sign_in_at`, both taken from
the Pi's clock and stored in MariaDB. A user's season total is their session
time plus any admin adjustments:

```sql
SELECT COALESCE((SELECT SUM(credited_seconds) FROM sessions
                 WHERE user_id = ? AND season_id = <active>), 0)
     + COALESCE((SELECT SUM(seconds) FROM adjustments
                 WHERE user_id = ? AND season_id = <active>), 0);
```

A subtraction that would take someone below zero is refused.

The leaderboard runs the same sum for all active users, sorts by total
(ties alphabetical) and gives tied users the same rank (1, 2, 2, 4). It is
computed fresh on every scan, never cached or read from a card.

## Useful queries

```sql
-- Who is signed in
SELECT u.username, s.sign_in_at FROM sessions s JOIN users u ON u.id = s.user_id
WHERE s.sign_out_at IS NULL;

-- Session hours per user this season (without admin adjustments)
SELECT u.username, ROUND(SUM(s.credited_seconds) / 3600, 2) AS hours
FROM sessions s JOIN users u ON u.id = s.user_id
JOIN seasons se ON se.id = s.season_id AND se.is_active = 1
GROUP BY u.id ORDER BY hours DESC;

-- Every adjustment this season
SELECT a.created_at, CONCAT(u.section, LPAD(u.number, 2, '0')) AS id, u.username,
       a.seconds / 60 AS minutes, a.reason, a.created_via
FROM adjustments a JOIN users u ON u.id = a.user_id
JOIN seasons se ON se.id = a.season_id AND se.is_active = 1
ORDER BY a.created_at;
```

## Connection

Connection details come from `[database]` in `config.toml` (password can be
given as `NFC_LOGIN_DB_PASSWORD` instead). The app uses PyMySQL and opens a
short connection per operation, so restarting MariaDB doesn't need a kiosk
restart.
