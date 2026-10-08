# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Checks GitHub for a newer version of this install, and can install it."""

# The Pi runs from a git clone (scripts/setup-pi.sh). check() only fetches and
# counts new commits; nothing in the working copy changes. install() (admin menu,
# System > 2) fast-forwards, reinstalls Python packages if requirements changed,
# and updates the database. Offline or not a git clone: it quietly does nothing.

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[2]  # nfc_login/services/ -> repo folder
ONLINE_CHECK = ("github.com", 443)              # "online" = can reach GitHub

# Changes to these files mean extra steps after pulling.
REQUIREMENTS = ("requirements-pi.txt", "requirements.txt")
SETUP_FILES = ("scripts/setup-pi.sh", "scripts/install.sh", "scripts/nfc-login.service",
               "electron/package.json")
SETUP_NOTE = "Some setup steps changed too: run bash scripts/setup-pi.sh when you have a keyboard."
APP_NOTE = "The screen app changed too: restart the Pi (System menu) to finish."
# Written when setup steps change, so the System menu keeps reminding until
# setup-pi.sh has run again (it deletes this file). Listed in .gitignore.
SETUP_MARKER = ".setup-needed"

# Seconds before a command is given up on (pip on a Pi can be slow).
GIT_TIMEOUT = 60
PIP_TIMEOUT = 900
INIT_DB_TIMEOUT = 120


class UpdateError(Exception):
    """A short reason shown on the kiosk screen."""


@dataclass
class InstallResult:
    old: str          # short commit before
    new: str          # short commit after
    notes: list[str]  # extra lines for the screen, e.g. a setup-script note


class UpdateChecker:
    """Checks git for new commits every few hours and installs them on request."""

    def __init__(self, repo_dir: Path = REPO_DIR, check_hours: float = 6,
                 run=subprocess.run, online=None, config_path: str | Path | None = None,
                 python: str = sys.executable):
        self.repo_dir = Path(repo_dir)
        self.interval = check_hours * 3600
        self.config_path = config_path
        self.python = python
        self._run = run
        self._online = online or _online
        self.new_commits = 0          # from the last check; > 0 shows "⬇ Update"
        self.automatic = False       # start() was called: checks run by themselves
        self.installing = False
        self._stopped = threading.Event()
        # check() and install() must never run git at the same time.
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """True when there's an update to install (shows the icon by the clock)."""
        return self.new_commits > 0 and not self.installing

    @property
    def setup_needed(self) -> bool:
        """An installed update changed setup steps, and setup-pi.sh hasn't run since."""
        return (self.repo_dir / SETUP_MARKER).exists()

    def start(self) -> None:
        """Check in the background every ``check_hours``."""
        self.automatic = True
        threading.Thread(target=self._loop, daemon=True, name="updates").start()

    def stop(self) -> None:
        self._stopped.set()

    def _loop(self) -> None:
        # First check a minute after start, so it doesn't slow down booting.
        wait = 60
        while not self._stopped.wait(wait):  # sleeps, but stop() wakes it early
            try:
                self.check()
            except Exception:
                log.exception("update check failed")
            wait = self.interval

    def _is_clone(self) -> bool:
        # Checked first: without it git would find any repo in a parent folder.
        return (self.repo_dir / ".git").exists()

    # ------------------------------------------------------------ checking

    def check(self, wait: bool = False) -> int | None:
        """Fetch and count new commits. None if it couldn't tell (offline etc.)."""
        # The background check skips a turn if git is busy. wait=True (the admin
        # menu) waits for another check instead, but not for an install.
        if self.installing:
            if wait:
                raise UpdateError("An update is being installed.")
            log.info("update check: an update is being installed, skipped")
            return None
        got = (self._lock.acquire(timeout=GIT_TIMEOUT * 2 + 10) if wait
               else self._lock.acquire(blocking=False))
        if not got:
            if wait:
                raise UpdateError("An update is being installed.")
            log.info("update check: an update is being installed, skipped")
            return None
        try:
            return self._check()
        finally:
            self._lock.release()

    def _check(self) -> int | None:
        if not self._is_clone():
            return None
        if not self._online():
            log.info("update check: offline, skipped")
            return None
        if self._git("fetch", "--quiet") is None:
            return None
        # HEAD..@{upstream}: commits on GitHub's branch that this copy doesn't have.
        count = self._git("rev-list", "--count", "HEAD..@{upstream}")
        if count is None or not count.isdigit():
            return None
        self.new_commits = int(count)
        log.info("update check: %s", f"{count} new change(s) on GitHub"
                 if self.new_commits else "up to date")
        return self.new_commits

    def pending_changes(self, limit: int = 5) -> list[str]:
        """Subjects of the new commits from the last check, newest first. [] if unknown."""
        # Read-only and doesn't fetch, so it doesn't need the lock.
        if limit <= 0 or not self._is_clone():
            return []
        out = self._git("log", "--format=%s", "-n", str(limit), "HEAD..@{upstream}")
        if out is None:
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]

    def _git(self, *args: str) -> str | None:
        """Run git; its output, or None (logged) if it failed."""
        try:
            result = self._call(["git", "-C", str(self.repo_dir), *args], GIT_TIMEOUT,
                                f"git {args[0]}")
        except UpdateError as exc:
            log.info("update check: git %s failed: %s", args[0], exc)
            return None
        if result.returncode != 0:
            log.info("update check: git %s failed: %s", args[0], result.stderr.strip())
            return None
        return result.stdout.strip()

    # ------------------------------------------------------------ installing

    def install(self) -> InstallResult:
        """Download and install the update. Raises UpdateError with a reason for the screen."""
        # Wait out a background check (its git calls time out after a minute).
        if not self._lock.acquire(timeout=GIT_TIMEOUT * 2 + 10):
            raise UpdateError("An update check is still running; try again in a few minutes.")
        self.installing = True
        try:
            return self._install()
        except UpdateError as exc:
            log.warning("update install failed: %s", exc)
            raise
        finally:
            self.installing = False
            self._lock.release()

    def _install(self) -> InstallResult:
        """Pull the new commits, then redo only the setup steps those commits need."""
        if not self._is_clone():
            raise UpdateError("This copy wasn't installed with git, so it can't update itself.")
        if not self._online():
            raise UpdateError("The Pi isn't online.")

        old = self._git_or_fail("Couldn't read the current version", "rev-parse", "HEAD")
        self._git_or_fail("Couldn't download the update", "fetch", "--quiet")
        # --ff-only never merges: with local edits in the way or a diverged history git
        # stops before touching anything, so the working copy is left as it was.
        self._git_or_fail("Couldn't apply the update", "merge", "--ff-only", "--quiet",
                          "@{upstream}")
        new = self._git_or_fail("Couldn't read the new version", "rev-parse", "HEAD")
        notes = [] if new != old else ["It was already up to date."]

        # Only redo the steps the new commits need.
        changed = self._changed_files(old)
        if changed is None or any(name in changed for name in REQUIREMENTS):
            self._install_packages(old)
        # Always run it, so tapping Install again retries a schema change that failed.
        self._apply_schema(old)
        if changed is None or any(name in changed for name in SETUP_FILES):
            notes.append(SETUP_NOTE)
            self._mark_setup_needed()
        elif (any(name.startswith("electron/") for name in changed)
              and not os.environ.get("NFC_KIOSK_CONTROL_FD")):
            # Without the app's control pipe only the kiosk program restarts, and the
            # screen app keeps running its old code until the Pi restarts.
            notes.append(APP_NOTE)

        self.new_commits = 0
        log.info("update installed: %s -> %s (%s file(s) changed)", old[:7], new[:7],
                 "?" if changed is None else len(changed))
        return InstallResult(old[:7], new[:7], notes)

    def _changed_files(self, old: str) -> set[str] | None:
        """Files changed since old, or None if git couldn't say (then assume anything did)."""
        out = self._git("diff", "--name-only", old, "HEAD")
        if out is None:
            log.warning("update install: couldn't list changed files, assuming all changed")
            return None
        return {line.strip() for line in out.splitlines() if line.strip()}

    def _install_packages(self, old: str) -> None:
        """pip install the requirements; on failure put the old code back."""
        cmd = [self.python, "-m", "pip", "install", "-r",
               str(self.repo_dir / "requirements-pi.txt")]
        log.info("update install: requirements changed, running pip")
        try:
            result = self._call(cmd, PIP_TIMEOUT, "pip")
            reason = None if result.returncode == 0 else _reason(result)
        except UpdateError as exc:
            reason = str(exc)
        if reason is None:
            return
        log.warning("update install: pip failed (%s), going back to %s", reason, old[:7])
        self._undo(old, "Couldn't install the new Python packages", reason)

    def _apply_schema(self, old: str) -> None:
        """Run init-db with the new code (schema statements are all IF NOT EXISTS)."""
        # If it fails the old code goes back, so a restart never runs new code on
        # a database it doesn't match.
        config = ["--config", str(self.config_path)] if self.config_path else []
        cmd = [self.python, "-m", "nfc_login.admin", *config, "init-db"]
        try:
            result = self._call(cmd, INIT_DB_TIMEOUT, "init-db")
            reason = None if result.returncode == 0 else _reason(result)
        except UpdateError as exc:
            reason = str(exc)
        if reason is None:
            return
        log.warning("update install: init-db failed (%s), going back to %s", reason, old[:7])
        self._undo(old, "Couldn't update the database", reason)

    def _undo(self, old: str, what: str, reason: str) -> None:
        """Go back to the old commit, then raise UpdateError saying what failed."""
        # --keep moves back to the old commit but keeps any local edits.
        cmd = ["git", "-C", str(self.repo_dir), "reset", "--quiet", "--keep", old]
        try:
            result = self._call(cmd, GIT_TIMEOUT, "git reset")
            undo_error = None if result.returncode == 0 else _reason(result)
        except UpdateError as exc:
            undo_error = str(exc)
        if undo_error:
            log.error("update install: couldn't go back to %s: %s", old[:7], undo_error)
            raise UpdateError(f"{what} ({reason}), and undoing the update failed too: "
                              f"{undo_error}")
        raise UpdateError(f"{what}, so the update was undone: {reason}")

    def _mark_setup_needed(self) -> None:
        """Leave the marker file so the System menu reminds to run setup-pi.sh."""
        try:
            (self.repo_dir / SETUP_MARKER).write_text(SETUP_NOTE + "\n")
        except OSError as exc:
            log.warning("update install: couldn't write %s: %s", SETUP_MARKER, exc)

    def _git_or_fail(self, what: str, *args: str) -> str:
        """Run git; its output, or UpdateError("<what>: <git's error>")."""
        result = self._call(["git", "-C", str(self.repo_dir), *args], GIT_TIMEOUT,
                            f"git {args[0]}")
        if result.returncode != 0:
            raise UpdateError(f"{what}: {_reason(result)}")
        return result.stdout.strip()

    def _call(self, cmd: list[str], timeout: float, name: str) -> subprocess.CompletedProcess:
        """Run a command in the repo folder; UpdateError if it can't start or hangs."""
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}   # never wait for a password
        try:
            return self._run(cmd, cwd=str(self.repo_dir), capture_output=True, text=True,
                             timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise UpdateError(f"{name} took too long and was stopped.") from None
        except OSError as exc:
            raise UpdateError(f"Couldn't run {cmd[0]}: {exc.strerror or exc}") from None


def _reason(result, limit: int = 120) -> str:
    """The most useful line of a failed command's output, short enough for the screen."""
    lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines()
             if line.strip()]
    if not lines:
        return f"exit code {result.returncode}"
    # git and pip end with lines like "Aborting" or hints; their error line says more.
    errors = [line for line in lines if line.lower().startswith(("fatal:", "error:"))]
    line = errors[-1].split(":", 1)[1].strip() if errors else lines[-1]
    line = line.rstrip(" :") or lines[-1]
    return line if len(line) <= limit else line[:limit - 3].rstrip() + "..."


def _online() -> bool:
    """True if a connection to GitHub opens within 3 seconds."""
    try:
        with socket.create_connection(ONLINE_CHECK, timeout=3):
            return True
    except OSError:
        return False
