"""The update check (nfc_login/services/updates.py)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from nfc_login.services.updates import UpdateChecker


def fake_git(behind="0", fetch_ok=True):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd[3:])
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        if cmd[3] == "fetch":
            return SimpleNamespace(returncode=0 if fetch_ok else 128, stdout="",
                                   stderr="" if fetch_ok else "could not resolve host")
        return SimpleNamespace(returncode=0, stdout=behind + "\n", stderr="")
    return run, calls


def test_new_commits_on_github_show_an_update(tmp_path):
    (tmp_path / ".git").mkdir()
    run, calls = fake_git(behind="3")
    checker = UpdateChecker(tmp_path, run=run, online=lambda: True)
    assert checker.check() == 3 and checker.available
    assert calls == [["fetch", "--quiet"], ["rev-list", "--count", "HEAD..@{upstream}"]]


def test_up_to_date(tmp_path):
    (tmp_path / ".git").mkdir()
    run, _calls = fake_git(behind="0")
    checker = UpdateChecker(tmp_path, run=run, online=lambda: True)
    assert checker.check() == 0 and not checker.available


def test_offline_or_failing_fetch_or_no_clone_changes_nothing(tmp_path):
    run, calls = fake_git(behind="5")
    assert UpdateChecker(tmp_path, run=run, online=lambda: True).check() is None   # no .git
    (tmp_path / ".git").mkdir()
    checker = UpdateChecker(tmp_path, run=run, online=lambda: False)
    assert checker.check() is None and calls == []                                 # offline
    run, _calls = fake_git(behind="5", fetch_ok=False)
    checker = UpdateChecker(tmp_path, run=run, online=lambda: True)
    assert checker.check() is None and not checker.available


def test_git_hanging_is_cut_off(tmp_path):
    (tmp_path / ".git").mkdir()

    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    assert UpdateChecker(tmp_path, run=run, online=lambda: True).check() is None
