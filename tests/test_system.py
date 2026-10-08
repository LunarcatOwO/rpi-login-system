# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""The kiosk's system actions (nfc_login/services/system.py), with fake commands."""

from __future__ import annotations

import logging
import subprocess
from types import SimpleNamespace

import pytest

from nfc_login.services.system import (
    NETWORK_GONE,
    NMCLI_MISSING,
    WIFI_OFF,
    WRONG_PASSWORD,
    SystemActionError,
    SystemActions,
    WifiNetwork,
    WifiOffError,
)

PASSWORD = "hunter2-secret"
SCAN = ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
        "--rescan"]
CONNECT = ["nmcli", "--wait", "30", "device", "wifi", "connect"]
ASK = ["nmcli", "--ask", "--wait", "30", "device", "wifi", "connect"]     # with a password
SAVED = ["nmcli", "-t", "-f", "NAME", "connection", "show"]
RADIO = ["nmcli", "-t", "-f", "WIFI-HW,WIFI", "radio"]


def ok(out=""):
    return SimpleNamespace(returncode=0, stdout=out, stderr="")


def fail(err, code=1):
    return SimpleNamespace(returncode=code, stdout="", stderr=err)


class FakeRun:
    """Stands in for subprocess.run. Rules are (argv prefix, answer); the first matching
    prefix answers with a result or raises an exception. Unmatched commands are "not
    installed"."""

    def __init__(self, *rules):
        self.rules = list(rules)
        self.calls = []
        self.kwargs = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        for prefix, answer in self.rules:
            if list(argv[:len(prefix)]) == list(prefix):
                if isinstance(answer, BaseException):
                    raise answer
                return answer
        raise FileNotFoundError(2, "No such file or directory", argv[0])


def files(contents):
    def read_text(path):
        if str(path) not in contents:
            raise FileNotFoundError(2, "No such file or directory", str(path))
        return contents[str(path)]
    return read_text


def actions(run, read_text=None, ip="192.168.1.50", tmp_path=None):
    return SystemActions(repo_dir=tmp_path or ".", run=run, read_text=read_text or files({}),
                         ip=lambda: ip, disk_usage=lambda _path: SimpleNamespace(free=12.3e9))


# -- Scanning -------------------------------------------------------------------


def test_scan_parses_escaped_colons_and_backslashes():
    run = FakeRun((SCAN, ok("*:Home\\:Net:72:WPA2\n"
                            ":Cafe\\\\Guest:40:--\n"
                            ":Open Spot:55:\n"
                            ":Office:30:WPA1 WPA2\n")))
    assert actions(run).wifi_networks() == [
        WifiNetwork("Home:Net", 72, secured=True, in_use=True),
        WifiNetwork("Open Spot", 55, secured=False, in_use=False),
        WifiNetwork("Cafe\\Guest", 40, secured=False, in_use=False),
        WifiNetwork("Office", 30, secured=True, in_use=False),
    ]
    assert run.calls == [[*SCAN, "yes"]]
    assert run.kwargs[0]["timeout"] == 20 and run.kwargs[0]["capture_output"]
    assert run.kwargs[0]["text"] and run.kwargs[0]["env"]["LC_ALL"].startswith("C")


def test_scan_merges_access_points_skips_hidden_and_sorts():
    run = FakeRun((SCAN, ok(":Office:30:WPA2\n"
                            "::90:WPA2\n"               # hidden network: no name
                            ":Gym:65:WPA3\n"
                            "*:Office:20:WPA2\n"        # in use, but a weaker access point
                            ":Office:80:WPA2\n"
                            ":Lab:65:WPA2\n"
                            "garbage line\n")))
    assert actions(run).wifi_networks() == [
        WifiNetwork("Office", 80, secured=True, in_use=True),
        WifiNetwork("Gym", 65, secured=True, in_use=False),
        WifiNetwork("Lab", 65, secured=True, in_use=False),
    ]


def test_scan_with_nothing_around_is_an_empty_list():
    run = FakeRun((SCAN, ok("")), (RADIO, ok("enabled:enabled\n")))
    assert actions(run).wifi_networks() == []


def test_scan_without_nmcli_says_so():
    run = FakeRun()
    with pytest.raises(SystemActionError) as caught:
        actions(run).wifi_networks()
    assert str(caught.value) == NMCLI_MISSING


def test_scan_with_wifi_switched_off():
    run = FakeRun((SCAN, fail("Error: Scanning not allowed while unavailable.")),
                  (RADIO, ok("enabled:disabled\n")),
                  (["nmcli", "radio", "wifi", "on"], ok()))
    with pytest.raises(WifiOffError) as caught:
        actions(run).wifi_networks()
    assert str(caught.value) == WIFI_OFF
    actions(run).wifi_on()
    assert run.calls[-1] == ["nmcli", "radio", "wifi", "on"]


def test_refused_scan_falls_back_to_the_networks_seen_last():
    run = FakeRun(([*SCAN, "yes"], fail("Error: Scanning not allowed immediately following "
                                         "previous scan.")),
                  ([*SCAN, "no"], ok(":Office:50:WPA2\n")))
    assert actions(run).wifi_networks() == [WifiNetwork("Office", 50, True, False)]


def test_failed_scan_gives_a_short_reason():
    run = FakeRun((SCAN, fail("Error: NetworkManager is not running.")),
                  (RADIO, fail("Error: NetworkManager is not running.")))
    with pytest.raises(SystemActionError) as caught:
        actions(run).wifi_networks()
    assert str(caught.value) == "Wi-Fi scan failed: NetworkManager is not running."


def test_scan_hanging_is_cut_off():
    run = FakeRun((SCAN, subprocess.TimeoutExpired("nmcli", 20)), (RADIO, ok("enabled:enabled")))
    with pytest.raises(SystemActionError, match="took too long"):
        actions(run).wifi_networks()


def test_current_wifi():
    run = FakeRun((["nmcli", "-t", "-f", "ACTIVE,SSID"], ok("no:Other\nyes:Home\\:Net\n")))
    assert actions(run).current_wifi() == "Home:Net"
    assert run.calls[0][-2:] == ["--rescan", "no"]
    run = FakeRun((["nmcli", "-t", "-f", "ACTIVE,SSID"], ok("no:Other\n")))
    assert actions(run).current_wifi() is None
    assert actions(FakeRun()).current_wifi() is None                  # no nmcli


# -- Connecting -----------------------------------------------------------------


def test_connect_success_returns_the_ip():
    run = FakeRun((SAVED, ok("Wired connection 1\n")), (ASK, ok("Device 'wlan0' ok.\n")))
    assert actions(run, ip="10.0.0.7").wifi_connect("Cafe Net", PASSWORD) == "10.0.0.7"
    # The password goes on stdin, where nmcli --ask reads it, never on the command line.
    assert run.calls[-1] == [*ASK, "Cafe Net"]
    assert run.kwargs[-1]["input"] == PASSWORD + "\n"
    assert run.kwargs[-1]["timeout"] == 45


def test_connect_to_an_open_network_sends_no_password():
    run = FakeRun((SAVED, ok("")), (CONNECT, ok()))
    assert actions(run, ip="").wifi_connect("Open Spot", None) == ""
    assert run.calls[-1] == [*CONNECT, "Open Spot"]
    assert run.kwargs[-1]["stdin"] == subprocess.DEVNULL and "input" not in run.kwargs[-1]


@pytest.mark.parametrize("err", [
    "Error: Connection activation failed: (7) Secrets were required, but not provided.",
    "Error: 802-11-wireless-security.psk: property is invalid.",
    "Error: Connection activation failed: No suitable secrets found.",
])
def test_wrong_password(err, caplog):
    caplog.set_level(logging.DEBUG)
    run = FakeRun((SAVED, ok("Wired connection 1\n")), (ASK, fail(err, 4)),
                  (["nmcli", "connection", "delete"], ok()))
    with pytest.raises(SystemActionError) as caught:
        actions(run).wifi_connect("Cafe Net", PASSWORD)
    assert str(caught.value) == WRONG_PASSWORD
    # The half-made profile with the wrong password isn't kept.
    assert run.calls[-1] == ["nmcli", "connection", "delete", "id", "Cafe Net"]
    assert PASSWORD not in caplog.text


def test_failed_connect_keeps_a_profile_that_was_already_saved():
    run = FakeRun((SAVED, ok("Cafe Net\n")),
                  (ASK, fail("Error: Connection activation failed: (7) Secrets were "
                                 "required, but not provided.")))
    with pytest.raises(SystemActionError):
        actions(run).wifi_connect("Cafe Net", PASSWORD)
    assert not any(call[:3] == ["nmcli", "connection", "delete"] for call in run.calls)


def test_secured_network_without_a_password():
    run = FakeRun((SAVED, ok("Cafe Net\n")),
                  (CONNECT, fail("Error: Connection activation failed: (7) Secrets were "
                                 "required, but not provided.")))
    with pytest.raises(SystemActionError, match="needs a password"):
        actions(run).wifi_connect("Cafe Net", None)


def test_network_gone():
    run = FakeRun((SAVED, ok("Cafe Net\n")),
                  (ASK, fail("Error: No network with SSID 'Cafe Net' found.", 10)))
    with pytest.raises(SystemActionError) as caught:
        actions(run).wifi_connect("Cafe Net", PASSWORD)
    assert str(caught.value) == NETWORK_GONE


def test_permission_error_retries_once_with_sudo():
    denied = fail("Error: Failed to add/activate new connection: Not authorized to control "
                  "networking.")
    run = FakeRun((SAVED, ok("")), (ASK, denied), (["sudo", "-n", "nmcli"], ok()))
    assert actions(run).wifi_connect("Cafe Net", PASSWORD) == "192.168.1.50"
    assert run.calls[-2:] == [[*ASK, "Cafe Net"], ["sudo", "-n", *ASK, "Cafe Net"]]
    assert run.kwargs[-1]["input"] == PASSWORD + "\n"


def test_permission_error_when_sudo_wants_a_password():
    run = FakeRun((SAVED, ok("Cafe Net\n")),
                  (ASK, fail("Error: Insufficient privileges.")),
                  (["sudo", "-n"], fail("sudo: a password is required")))
    with pytest.raises(SystemActionError, match="isn't allowed to change Wi-Fi"):
        actions(run).wifi_connect("Cafe Net", PASSWORD)
    assert sum(call[0] == "sudo" for call in run.calls) == 1        # retried only once


def test_other_errors_show_nmclis_last_line_trimmed():
    long_reason = "Connection activation failed: " + "the access point went away " * 5
    run = FakeRun((SAVED, ok("Cafe Net\n")),
                  (ASK, fail(f"Warning: something minor\nError: {long_reason}\n")))
    with pytest.raises(SystemActionError) as caught:
        actions(run).wifi_connect("Cafe Net", PASSWORD)
    message = str(caught.value)
    assert message.startswith("Connection activation failed: the access point")
    assert len(message) <= 80 and "\n" not in message


def test_password_never_leaks_into_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    # An error that echoes the password back must not reach the screen.
    run = FakeRun((SAVED, ok("Cafe Net\n")),
                  (ASK, fail(f"Error: invalid argument '{PASSWORD}'.")))
    with pytest.raises(SystemActionError) as caught:
        actions(run).wifi_connect("Cafe Net", PASSWORD)
    assert PASSWORD not in str(caught.value)
    # A hang: TimeoutExpired carries the whole command line, password included.
    run = FakeRun((SAVED, ok("Cafe Net\n")),
                  (ASK, subprocess.TimeoutExpired([*ASK, "Cafe Net"], 45)))
    with pytest.raises(SystemActionError) as caught:
        actions(run).wifi_connect("Cafe Net", PASSWORD)
    assert "didn't answer in time" in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert PASSWORD not in caplog.text


def test_connect_without_nmcli():
    with pytest.raises(SystemActionError) as caught:
        actions(FakeRun()).wifi_connect("Cafe Net", PASSWORD)
    assert str(caught.value) == NMCLI_MISSING


# -- Power ----------------------------------------------------------------------


def test_power_uses_sudo_first():
    run = FakeRun((["sudo", "-n", "systemctl"], ok()))
    actions(run).power("reboot")
    assert run.calls == [["sudo", "-n", "systemctl", "reboot"]]


def test_power_falls_back_to_plain_systemctl():
    run = FakeRun((["sudo"], fail("sudo: a password is required")),
                  (["systemctl", "--no-ask-password"], ok()))
    actions(run).power("poweroff")
    assert run.calls == [["sudo", "-n", "systemctl", "poweroff"],
                         ["systemctl", "--no-ask-password", "poweroff"]]


def test_power_failure_says_why():
    run = FakeRun((["sudo"], fail("sudo: a password is required")),
                  (["systemctl"], fail("Failed to reboot system via logind: Interactive "
                                       "authentication required.")))
    with pytest.raises(SystemActionError) as caught:
        actions(run).power("reboot")
    assert str(caught.value) == "Couldn't restart the Pi: the kiosk isn't allowed to."
    with pytest.raises(SystemActionError) as caught:
        actions(FakeRun()).power("poweroff")                       # no sudo, no systemd
    assert str(caught.value) == "Couldn't shut down the Pi: systemctl isn't installed"


def test_power_rejects_other_actions():
    run = FakeRun()
    with pytest.raises(ValueError):
        actions(run).power("halt")
    assert run.calls == []


# -- System info ----------------------------------------------------------------


def test_version_from_git(tmp_path):
    run = FakeRun((["git"], ok("c86efbe 2026-10-02\n")))
    assert actions(run, tmp_path=tmp_path).version() == "c86efbe (2 Oct 2026)"
    assert run.calls == [["git", "-C", str(tmp_path), "log", "-1", "--format=%h %cs"]]
    assert run.kwargs[0]["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_version_unknown_when_git_fails():
    not_a_clone = FakeRun((["git"], fail("fatal: not a git repository", 128)))
    assert actions(not_a_clone).version() == "unknown"
    assert actions(FakeRun()).version() == "unknown"                 # git not installed
    hang = FakeRun((["git"], subprocess.TimeoutExpired("git", 10)))
    assert actions(hang).version() == "unknown"


def test_info_lines_on_a_pi():
    run = FakeRun((["nmcli", "-t", "-f", "ACTIVE,SSID"], ok("yes:Workshop\n")),
                  (["git"], ok("c86efbe 2026-10-02\n")),
                  (["vcgencmd"], ok("throttled=0x0\n")))
    read_text = files({"/sys/class/thermal/thermal_zone0/temp": "48234\n",
                       "/proc/uptime": "11545.20 40000.00\n"})
    lines = actions(run, read_text, ip="192.168.1.50").info_lines()
    assert lines[0].startswith("Host: ")
    assert lines[1:] == ["IP: 192.168.1.50", "Wi-Fi: Workshop", "Version: c86efbe (2 Oct 2026)",
                         "CPU temp: 48.2 °C", "Free space: 12.3 GB", "Up: 3h 12m"]


def test_info_lines_with_nothing_available():
    lines = actions(FakeRun(), files({}), ip="").info_lines()
    assert lines[0].startswith("Host: ")
    assert lines[1:] == ["IP: not connected", "Wi-Fi: not connected", "Version: unknown",
                         "Free space: 12.3 GB"]


def test_info_lines_never_raise_and_stay_short():
    def broken_disk(_path):
        raise OSError("gone")

    def garbage(_path):
        return "not a number"

    long_name = "A Very Long Network Name Indeed 32"
    run = FakeRun((["nmcli", "-t", "-f", "ACTIVE,SSID"], ok(f"yes:{long_name}\n")),
                  (["vcgencmd"], ok("throttled=0x50005\n")))
    system = SystemActions(run=run, read_text=garbage, ip=lambda: "10.0.0.7",
                           disk_usage=broken_disk)
    lines = system.info_lines()
    assert all(len(line) <= 40 for line in lines)
    assert lines[2].startswith("Wi-Fi: A Very Long") and lines[2].endswith("…")
    assert "Power supply: too weak now!" in lines
    assert not any(line.startswith(("CPU", "Up", "Free")) for line in lines)


def test_uptime_formats():
    for seconds, text in [(59, "Up: 0m"), (600, "Up: 10m"), (3 * 86400 + 7200, "Up: 3d 2h")]:
        read_text = files({"/proc/uptime": f"{seconds}.5 1.0\n"})
        assert text in actions(FakeRun(), read_text).info_lines()
