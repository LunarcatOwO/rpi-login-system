"""What the kiosk admin menu can do to the Pi itself: system info, Wi-Fi, restart.

The kiosk has no keyboard, so this is how an admin puts the Pi on a new Wi-Fi
network, finds its IP address or restarts it from the touchscreen. It drives
the Raspberry Pi OS (Bookworm) tools: NetworkManager's nmcli and systemd, with
`sudo -n` where the desktop user needs it. On a dev PC those tools may be
missing: the actions then raise SystemActionError with a short reason, and the
info lines leave out what they can't find.

The Wi-Fi and power calls block (a scan takes a few seconds, connecting up to
45 s), so call them off the UI thread.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[2]     # same as updates.REPO_DIR

NMCLI_MISSING = "Wi-Fi setup needs NetworkManager (nmcli), which this Pi doesn't have."
WIFI_OFF = "Wi-Fi is turned off on this Pi."
WRONG_PASSWORD = "Wrong Wi-Fi password, or the network refused it."
NETWORK_GONE = "Couldn't find that network any more."
NOT_ALLOWED = "The kiosk isn't allowed to change Wi-Fi on this Pi."

THERMAL_FILE = "/sys/class/thermal/thermal_zone0/temp"
UPTIME_FILE = "/proc/uptime"
INFO_WIDTH = 40          # the System info screen fits about this many characters a line
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Exit codes for "couldn't run it" and "took too long", like a shell uses.
NOT_FOUND = 127
TIMED_OUT = 124

# C.UTF-8 rather than plain C: English messages, but Wi-Fi names with accents or
# emoji still come through intact.
_ENV = {"LC_ALL": "C.UTF-8", "GIT_TERMINAL_PROMPT": "0"}
_SCAN = ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
         "--rescan"]
_POWER = {"reboot": "restart", "poweroff": "shut down"}

_PERMISSION_HINTS = ("not authorized", "insufficient privileges")
# "802-11-wireless-security.psk: property is invalid" etc.: a password nmcli won't take.
_WRONG_PASSWORD_HINTS = ("secrets were required", "no suitable secrets",
                         "802-11-wireless-security.")
_GONE_HINTS = ("no network with ssid", "network could not be found")
_POLKIT_HINTS = ("authentication required", "access denied", "not authorized")


class SystemActionError(Exception):
    """A friendly, one-line reason shown on the kiosk screen."""


class WifiOffError(SystemActionError):
    """The Wi-Fi radio is switched off; SystemActions.wifi_on() can fix that."""


@dataclass
class WifiNetwork:
    """One network name from a scan (several access points with that name count once)."""
    ssid: str
    signal: int        # 0-100
    secured: bool
    in_use: bool


class _Result(NamedTuple):
    code: int          # 0 = worked; NOT_FOUND / TIMED_OUT when it didn't run or finish
    out: str
    err: str


def local_ip() -> str:
    """This machine's LAN address, "" if it has none. Sends no packets."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            address = s.getsockname()[0]
    except OSError:
        return ""
    return "" if address == "0.0.0.0" else address


def _read_text(path: str | Path) -> str:
    return Path(path).read_text()


class SystemActions:
    def __init__(self, repo_dir: Path = REPO_DIR, run=subprocess.run, read_text=_read_text,
                 ip=local_ip, disk_usage=shutil.disk_usage):
        self.repo_dir = Path(repo_dir)
        self._run = run
        self._read_text = read_text
        self._ip = ip
        self._disk_usage = disk_usage

    # -- System info ---------------------------------------------------------------

    def version(self) -> str:
        """The running code's commit and date, e.g. "c86efbe (2 Oct 2026)"."""
        result = self._exec(["git", "-C", str(self.repo_dir), "log", "-1", "--format=%h %cs"],
                            10)
        words = result.out.split() if result.code == 0 else []
        if not words:
            return "unknown"
        try:
            day = date.fromisoformat(words[1])
        except (IndexError, ValueError):
            return words[0]
        return f"{words[0]} ({day.day} {MONTHS[day.month - 1]} {day.year})"

    def info_lines(self) -> list[str]:
        """Short lines for the System info screen. Never raises; unknown bits are left out."""
        lines = [
            _quiet(lambda: f"Host: {socket.gethostname()}"),
            f"IP: {_quiet(self._ip) or 'not connected'}",
            f"Wi-Fi: {_quiet(self.current_wifi) or 'not connected'}",
            f"Version: {_quiet(self.version) or 'unknown'}",
            _quiet(self._cpu_temp_line),
            _quiet(self._free_space_line),
            _quiet(self._uptime_line),
            _quiet(self._power_line),
        ]
        return [_trim(line, INFO_WIDTH) for line in lines if line]

    def _cpu_temp_line(self) -> str:
        millidegrees = int(self._read_text(THERMAL_FILE).strip())
        return f"CPU temp: {millidegrees / 1000:.1f} °C"

    def _free_space_line(self) -> str:
        return f"Free space: {self._disk_usage(self.repo_dir).free / 1e9:.1f} GB"

    def _uptime_line(self) -> str:
        minutes = int(float(self._read_text(UPTIME_FILE).split()[0])) // 60
        days, minutes = divmod(minutes, 24 * 60)
        hours, minutes = divmod(minutes, 60)
        if days:
            return f"Up: {days}d {hours}h"
        return f"Up: {hours}h {minutes}m" if hours else f"Up: {minutes}m"

    def _power_line(self) -> str | None:
        """Only shown when the Pi's power supply has been too weak (a common cause of
        flaky card reads); None when all is well or this isn't a Pi."""
        result = self._exec(["vcgencmd", "get_throttled"], 5)
        if result.code != 0 or "=" not in result.out:
            return None
        flags = int(result.out.split("=", 1)[1].strip(), 16)
        if flags & 0x1:
            return "Power supply: too weak now!"
        if flags & 0x10000:
            return "Power supply: dipped since boot"
        return None

    # -- Wi-Fi ---------------------------------------------------------------------

    def current_wifi(self) -> str | None:
        """Name of the Wi-Fi network the Pi is on, None if none (or no nmcli)."""
        # --rescan no: answer from what NetworkManager already knows, instantly.
        result = self._exec(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list",
                             "--rescan", "no"], 5)
        if result.code != 0:
            return None
        for line in result.out.splitlines():
            fields = _terse_fields(line)
            if len(fields) == 2 and fields[0] == "yes" and fields[1]:
                return fields[1]
        return None

    def wifi_networks(self) -> list[WifiNetwork]:
        """Scan for networks: the one in use first, then strongest first."""
        result = self._nmcli([*_SCAN, "yes"], 20)
        if result.code not in (0, NOT_FOUND, TIMED_OUT):
            # NetworkManager sometimes refuses a fresh scan (one just ran, or it's busy
            # connecting); what it saw last time is still worth showing.
            cached = self._nmcli([*_SCAN, "no"], 10)
            if cached.code == 0 and cached.out.strip():
                result = cached
        if result.code == NOT_FOUND:
            raise SystemActionError(NMCLI_MISSING)
        networks = _parse_scan(result.out) if result.code == 0 else []
        if not networks and self._wifi_off():
            raise WifiOffError(WIFI_OFF)
        if result.code != 0:
            raise SystemActionError(_scan_error(result))
        log.info("Wi-Fi scan: %d network(s)", len(networks))
        return networks

    def wifi_on(self) -> None:
        """Switch the Wi-Fi radio on. It takes a few seconds before a scan finds anything."""
        result = self._nmcli(["nmcli", "radio", "wifi", "on"], 15)
        if result.code == NOT_FOUND:
            raise SystemActionError(NMCLI_MISSING)
        if result.code != 0:
            reason = _short(result.err or result.out, 50) or "unknown error"
            raise SystemActionError(f"Couldn't turn Wi-Fi on: {reason}")
        log.info("Wi-Fi radio switched on")

    def wifi_connect(self, ssid: str, password: str | None) -> str:
        """Join a network (the Pi remembers it). Returns the Pi's IP address, "" if unknown.

        The password goes to nmcli on its standard input, where it asks for it
        (--ask): never on a command line (which any program on the Pi can read,
        and sudo logs), never logged or put in an error.
        """
        if not ssid:
            raise SystemActionError("Pick a Wi-Fi network first.")
        argv = ["nmcli", "--wait", "30", "device", "wifi", "connect", ssid]
        if password:
            argv.insert(1, "--ask")
        saved = self._saved_connections()
        log.info("Wi-Fi: connecting to %r", ssid)
        result = self._nmcli(argv, 45, password + "\n" if password else None)
        if result.code == 0:
            ip = _quiet(self._ip) or ""
            log.info("Wi-Fi: connected to %r, IP %s", ssid, ip or "unknown")
            return ip
        if saved is not None and ssid not in saved:
            # Don't keep the profile nmcli just made (say, with a mistyped password):
            # the Pi would keep trying it at every boot.
            self._nmcli(["nmcli", "connection", "delete", "id", ssid], 15)
        message = _connect_error(result, password)
        log.info("Wi-Fi: couldn't connect to %r: %s", ssid, message)
        raise SystemActionError(message)

    def _saved_connections(self) -> set[str] | None:
        """Names of the network profiles NetworkManager has saved, None if unknown."""
        result = self._exec(["nmcli", "-t", "-f", "NAME", "connection", "show"], 10)
        if result.code != 0:
            return None
        return {_terse_fields(line)[0] for line in result.out.splitlines() if line}

    def _wifi_off(self) -> bool:
        result = self._exec(["nmcli", "-t", "-f", "WIFI-HW,WIFI", "radio"], 5)
        return result.code == 0 and "disabled" in _terse_fields(result.out.strip())

    # -- Power ---------------------------------------------------------------------

    def power(self, action: str) -> None:
        """Restart ("reboot") or shut down ("poweroff") the Pi."""
        if action not in _POWER:
            raise ValueError(f"unknown power action: {action!r}")
        log.info("power: %s requested", action)
        # sudo first (the Pi's desktop user has it without a password); then plain
        # systemctl, which polkit allows for the user logged in at the screen.
        for argv in (["sudo", "-n", "systemctl", action],
                     ["systemctl", "--no-ask-password", action]):
            result = self._exec(argv, 30)
            if result.code == 0:
                return
        if result.code != NOT_FOUND and _mentions(result, _POLKIT_HINTS):
            reason = "the kiosk isn't allowed to."
        else:
            reason = _short(result.err or result.out, 50) or "unknown error"
        log.warning("power: %s failed: %s", action, reason)
        raise SystemActionError(f"Couldn't {_POWER[action]} the Pi: {reason}")

    # -- Running commands ----------------------------------------------------------

    def _exec(self, argv: list[str], timeout: float, stdin: str | None = None) -> _Result:
        """Run a command. Never raises: a missing tool or a hang comes back as a result."""
        env = {**os.environ, **_ENV}
        env.pop("LANGUAGE", None)       # it would override LC_ALL for messages
        try:
            # Nothing may ever wait for typing: without input, stdin is empty.
            feed = ({"input": stdin} if stdin is not None
                    else {"stdin": subprocess.DEVNULL})
            done = self._run(argv, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout, env=env, **feed)
        except FileNotFoundError:
            return _Result(NOT_FOUND, "", f"{argv[0]} isn't installed")
        except subprocess.TimeoutExpired:
            return _Result(TIMED_OUT, "", "it took too long")
        except OSError as exc:
            return _Result(126, "", exc.strerror or "couldn't run it")
        return _Result(done.returncode, done.stdout or "", done.stderr or "")

    def _nmcli(self, argv: list[str], timeout: float, stdin: str | None = None) -> _Result:
        """Run nmcli; if NetworkManager says we're not allowed, try once more with sudo."""
        result = self._exec(argv, timeout, stdin)
        if result.code in (0, NOT_FOUND, TIMED_OUT) or not _mentions(result, _PERMISSION_HINTS):
            return result
        retry = self._exec(["sudo", "-n", *argv], timeout, stdin)
        if retry.code == NOT_FOUND or retry.err.lstrip().startswith("sudo:"):
            return result       # no sudo, or it wanted a password: report the first error
        return retry


def _terse_fields(line: str) -> list[str]:
    """Split one line of `nmcli -t` output. Values escape ':' and '\\' with a backslash."""
    fields, field = [], []
    chars = iter(line)
    for ch in chars:
        if ch == "\\":
            field.append(next(chars, ""))
        elif ch == ":":
            fields.append("".join(field))
            field = []
        else:
            field.append(ch)
    fields.append("".join(field))
    return fields


def _parse_scan(output: str) -> list[WifiNetwork]:
    """IN-USE,SSID,SIGNAL,SECURITY lines -> one entry per network name, best first."""
    found: dict[str, WifiNetwork] = {}
    for line in output.splitlines():
        fields = _terse_fields(line)
        if len(fields) != 4 or fields[1].strip() in ("", "--"):
            continue        # a hidden network (no name), or a line we don't understand
        in_use, ssid, signal, security = fields
        network = WifiNetwork(ssid, _signal(signal), security.strip() not in ("", "--"),
                              in_use.strip() == "*")
        seen = found.get(ssid)
        if seen is None:
            found[ssid] = network
            continue
        # The same name on several access points (mesh, or 2.4 and 5 GHz).
        seen.signal = max(seen.signal, network.signal)
        seen.in_use = seen.in_use or network.in_use
        seen.secured = seen.secured or network.secured
    return sorted(found.values(), key=lambda n: (not n.in_use, -n.signal, n.ssid.lower()))


def _signal(text: str) -> int:
    try:
        return min(100, max(0, int(text.strip())))
    except ValueError:
        return 0


def _mentions(result: _Result, hints: tuple[str, ...]) -> bool:
    text = f"{result.err}\n{result.out}".lower()
    return any(hint in text for hint in hints)


def _scan_error(result: _Result) -> str:
    if result.code == TIMED_OUT:
        return "The Wi-Fi scan took too long. Try again."
    if _mentions(result, _PERMISSION_HINTS):
        return "The kiosk isn't allowed to scan for Wi-Fi on this Pi."
    return f"Wi-Fi scan failed: {_short(result.err or result.out, 60) or 'unknown error'}"


def _connect_error(result: _Result, password: str | None) -> str:
    if result.code == NOT_FOUND:
        return NMCLI_MISSING
    if result.code == TIMED_OUT or _mentions(result, ("timeout expired",)):
        return "The network didn't answer in time. Try again."
    if _mentions(result, ("802-1x",)):
        return "That network needs a username too (enterprise Wi-Fi); not supported."
    if _mentions(result, _WRONG_PASSWORD_HINTS):
        return WRONG_PASSWORD if password else "That network needs a password."
    if _mentions(result, _GONE_HINTS):
        return NETWORK_GONE
    if _mentions(result, _PERMISSION_HINTS):
        return NOT_ALLOWED
    message = _short(result.err or result.out)
    if not message or (password and password in message):
        return "Couldn't connect to that network."
    return message


def _short(text: str, limit: int = 80) -> str:
    """The last line of a command's error output, without "Error: ", cut to fit."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    last = lines[-1].removeprefix("Error: ") if lines else ""
    return _trim(last, limit)


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _quiet(func):
    """func(), or None if it raised: for the info screen, which must always show."""
    try:
        return func()
    except Exception:
        log.debug("system info: %s failed", getattr(func, "__name__", func), exc_info=True)
        return None
