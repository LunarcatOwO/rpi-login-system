"""Checks GitHub for a newer version of this install, so the kiosk can say so.

The Pi runs from a git clone (scripts/setup-pi.sh). Every few hours, if the
Pi is online, this fetches the branch it follows and counts the new commits.
It only looks: nothing is downloaded into the working copy or installed.
Offline, or not a git clone, it quietly does nothing.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[2]
ONLINE_CHECK = ("github.com", 443)


class UpdateChecker:
    def __init__(self, repo_dir: Path = REPO_DIR, check_hours: float = 6,
                 run=subprocess.run, online=None):
        self.repo_dir = Path(repo_dir)
        self.interval = check_hours * 3600
        self._run = run
        self._online = online or _online
        self.new_commits = 0
        self._stopped = threading.Event()

    @property
    def available(self) -> bool:
        return self.new_commits > 0

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="updates").start()

    def stop(self) -> None:
        self._stopped.set()

    def _loop(self) -> None:
        # First check a minute after start, so it doesn't slow down booting.
        wait = 60
        while not self._stopped.wait(wait):
            try:
                self.check()
            except Exception:
                log.exception("update check failed")
            wait = self.interval

    def check(self) -> int | None:
        """Fetch and count new commits. None if it couldn't tell (offline etc.)."""
        if not (self.repo_dir / ".git").exists():
            return None
        if not self._online():
            log.info("update check: offline, skipped")
            return None
        if self._git("fetch", "--quiet") is None:
            return None
        count = self._git("rev-list", "--count", "HEAD..@{upstream}")
        if count is None or not count.isdigit():
            return None
        self.new_commits = int(count)
        log.info("update check: %s", f"{count} new change(s) on GitHub"
                 if self.new_commits else "up to date")
        return self.new_commits

    def _git(self, *args: str) -> str | None:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}   # never wait for a password
        try:
            result = self._run(["git", "-C", str(self.repo_dir), *args], capture_output=True,
                               text=True, timeout=60, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.info("update check: git %s failed: %s", args[0], exc)
            return None
        if result.returncode != 0:
            log.info("update check: git %s failed: %s", args[0], result.stderr.strip())
            return None
        return result.stdout.strip()


def _online() -> bool:
    try:
        with socket.create_connection(ONLINE_CHECK, timeout=3):
            return True
    except OSError:
        return False
