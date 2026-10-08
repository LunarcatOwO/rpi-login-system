-- rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
-- Created by LunarcatOwO (https://github.com/LunarcatOwO)
-- Copyright (C) 2026 LunarcatOwO
-- SPDX-License-Identifier: GPL-3.0-or-later
--
-- This program is free software: you can redistribute it and/or modify it
-- under the terms of the GNU General Public License as published by the Free
-- Software Foundation, either version 3 of the License, or (at your option)
-- any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

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
-- Their user ID is team letter + three-digit number, e.g. A007 (section 'A', number 7);
-- mentors (section 'M') have just the number, e.g. 007.
-- `id` is only an internal key and is never shown.
CREATE TABLE IF NOT EXISTS users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    section     CHAR(1)      NOT NULL,
    number      INT          NOT NULL,
    username    VARCHAR(64)  NOT NULL UNIQUE,
    pin_hash    VARCHAR(255) NULL,          -- optional keypad PIN (PBKDF2)
    is_active   TINYINT(1)   NOT NULL DEFAULT 1,
    created_at  DATETIME     NOT NULL,
    UNIQUE KEY uq_users_code (section, number)
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

-- Manual corrections by an admin. Positive seconds add time, negative subtract.
-- A season total is SUM(sessions.credited_seconds) + SUM(adjustments.seconds).
CREATE TABLE IF NOT EXISTS adjustments (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT          NOT NULL,
    season_id   INT          NOT NULL,
    seconds     INT          NOT NULL,
    reason      VARCHAR(255) NOT NULL DEFAULT '',
    created_at  DATETIME     NOT NULL,
    created_via VARCHAR(16)  NOT NULL,      -- kiosk | web | cli
    CONSTRAINT fk_adjustments_user   FOREIGN KEY (user_id)   REFERENCES users(id),
    CONSTRAINT fk_adjustments_season FOREIGN KEY (season_id) REFERENCES seasons(id),
    INDEX idx_adjustments_user_season (user_id, season_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Visit log copied from the legacy system (aesom-e/attendance `records` table).
-- Kept for reference only: imported hours are added as adjustments instead.
CREATE TABLE IF NOT EXISTS legacy_records (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id           INT          NOT NULL,
    legacy_record_id  INT UNSIGNED NOT NULL UNIQUE,
    start_time        DATETIME     NULL,
    end_time          DATETIME     NULL,
    notes             VARCHAR(64)  NULL,
    CONSTRAINT fk_legacy_records_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Columns added after the first release. ADD COLUMN IF NOT EXISTS keeps this
-- file safe to re-run on an existing database.
-- users.legacy_id: the userId in the legacy system, so an import can be re-run.
ALTER TABLE users ADD COLUMN IF NOT EXISTS legacy_id INT UNSIGNED NULL UNIQUE;
-- tags.legacy_key: the number the legacy MFRC522 script stored for this card
-- (4 UID bytes + check byte). Lets old cards and RC522 readers match by number.
ALTER TABLE tags ADD COLUMN IF NOT EXISTS legacy_key BIGINT UNSIGNED NULL;
CREATE INDEX IF NOT EXISTS idx_tags_legacy_key ON tags (legacy_key);

-- Small key/value store (admin PIN hash, etc.).
CREATE TABLE IF NOT EXISTS settings (
    name   VARCHAR(64)  PRIMARY KEY,
    value  VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
