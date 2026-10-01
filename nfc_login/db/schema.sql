-- NFC login system schema (MariaDB 10.5+).
-- Applied by `python3 -m nfc_login.admin init-db`; safe to run again.

-- A season is one tracking period (normally a year). Exactly one is active.
-- Old seasons are never deleted: their sessions stay linked to them.
CREATE TABLE IF NOT EXISTS seasons (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64) NOT NULL UNIQUE,
    started_at  DATETIME    NOT NULL,
    ended_at    DATETIME    NULL,
    is_active   TINYINT(1)  NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- People who sign in. Users carry over from season to season.
CREATE TABLE IF NOT EXISTS users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(64)  NOT NULL UNIQUE,
    pin_hash    VARCHAR(255) NULL,          -- optional keypad PIN (PBKDF2)
    is_active   TINYINT(1)   NOT NULL DEFAULT 1,
    created_at  DATETIME     NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- NFC tags, keyed by the tag's factory UID. The UID is read-only on genuine
-- tags, so it is what identifies a user; nothing written on the tag is trusted.
CREATE TABLE IF NOT EXISTS tags (
    uid          VARCHAR(32) PRIMARY KEY,   -- upper-case hex, e.g. 04A1B2C3D4E5F6
    user_id      INT         NOT NULL,
    enrolled_at  DATETIME    NOT NULL,
    is_active    TINYINT(1)  NOT NULL DEFAULT 1,
    CONSTRAINT fk_tags_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One row per sign-in. Hours are always SUM(credited_seconds) over a season.
CREATE TABLE IF NOT EXISTS sessions (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id           INT         NOT NULL,
    season_id         INT         NOT NULL,
    sign_in_at        DATETIME    NOT NULL,
    sign_in_method    VARCHAR(16) NOT NULL,      -- card | keypad
    sign_out_at       DATETIME    NULL,          -- NULL while signed in
    sign_out_method   VARCHAR(16) NULL,          -- card | keypad | timeout | season_reset | admin
    credited_seconds  INT         NULL,          -- set at sign-out
    CONSTRAINT fk_sessions_user   FOREIGN KEY (user_id)   REFERENCES users(id),
    CONSTRAINT fk_sessions_season FOREIGN KEY (season_id) REFERENCES seasons(id),
    INDEX idx_sessions_user_season (user_id, season_id),
    INDEX idx_sessions_open (sign_out_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Small key/value store (admin PIN hash, etc.).
CREATE TABLE IF NOT EXISTS settings (
    name   VARCHAR(64)  PRIMARY KEY,
    value  VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
