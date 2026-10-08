# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""The update check (nfc_login/services/updates.py)."""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from nfc_login.services.updates import (
    APP_NOTE,
    SETUP_NOTE,
    InstallResult,
    UpdateChecker,
    UpdateError,
)


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


# ---------------------------------------------------------------- pending changes


def test_pending_changes_lists_new_commit_subjects(tmp_path):
    (tmp_path / ".git").mkdir()
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd[3:])
        return SimpleNamespace(returncode=0, stdout="Fix the clock\n\nAdd a menu  \n", stderr="")
    checker = UpdateChecker(tmp_path, run=run, online=lambda: True)
    assert checker.pending_changes(3) == ["Fix the clock", "Add a menu"]
    assert calls == [["log", "--format=%s", "-n", "3", "HEAD..@{upstream}"]]   # no fetch


def test_pending_changes_is_empty_when_git_fails_or_no_clone(tmp_path):
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=128, stdout="", stderr="fatal: no upstream")
    assert UpdateChecker(tmp_path, run=run, online=lambda: True).pending_changes() == []
    (tmp_path / ".git").mkdir()
    assert UpdateChecker(tmp_path, run=run, online=lambda: True).pending_changes() == []

    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    assert UpdateChecker(tmp_path, run=hang, online=lambda: True).pending_changes() == []


# ---------------------------------------------------------------- install (fake commands)

OLD = "1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NEW = "2222222bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
PYTHON = "/opt/kiosk/.venv/bin/python"


class FakePi:
    """Answers git, pip and init-db like a Pi with new commits waiting on GitHub."""

    def __init__(self, changed=("nfc_login/app.py",), fail=None):
        self.changed = changed
        self.fail = fail or {}      # step -> (returncode, stderr), or an exception to raise
        self.head = OLD
        self.calls = []
        self.timeouts = {}

    def step(self, cmd):
        if cmd[0] == "git":
            return cmd[3]
        return "pip" if cmd[1:3] == ["-m", "pip"] else "init-db"

    def __call__(self, cmd, **kwargs):
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["capture_output"] and kwargs["text"] and kwargs["cwd"]
        step = self.step(cmd)
        self.calls.append(cmd)
        self.timeouts[step] = kwargs["timeout"]
        failure = self.fail.get(step)
        if isinstance(failure, BaseException):
            raise failure
        if failure:
            return SimpleNamespace(returncode=failure[0], stdout="", stderr=failure[1])
        stdout = ""
        if step == "rev-parse":
            stdout = self.head + "\n"
        elif step == "merge":
            self.head = NEW
        elif step == "reset":
            self.head = cmd[-1]
        elif step == "diff":
            stdout = "".join(name + "\n" for name in self.changed)
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    def steps(self):
        return [self.step(cmd) for cmd in self.calls]

    def call(self, step):
        return next(cmd for cmd in self.calls if self.step(cmd) == step)


def make_checker(tmp_path, pi, **kwargs):
    (tmp_path / ".git").mkdir(exist_ok=True)
    checker = UpdateChecker(tmp_path, run=pi, online=lambda: True, python=PYTHON, **kwargs)
    checker.new_commits = 3
    return checker


def test_install_without_requirement_changes_skips_pip(tmp_path):
    pi = FakePi(changed=["nfc_login/app.py", "README.md"])
    checker = make_checker(tmp_path, pi, config_path=tmp_path / "config.toml")
    result = checker.install()
    assert result == InstallResult(OLD[:7], NEW[:7], [])
    assert pi.steps() == ["rev-parse", "fetch", "merge", "rev-parse", "diff", "init-db"]
    assert pi.call("merge")[3:] == ["merge", "--ff-only", "--quiet", "@{upstream}"]
    assert pi.call("diff")[3:] == ["diff", "--name-only", OLD, "HEAD"]
    # --config is a global option, so it goes before the subcommand
    assert pi.call("init-db") == [PYTHON, "-m", "nfc_login.admin",
                                  "--config", str(tmp_path / "config.toml"), "init-db"]
    assert checker.new_commits == 0 and not checker.available


def test_install_with_new_requirements_runs_pip_with_the_kiosk_python(tmp_path):
    pi = FakePi(changed=["requirements.txt"])
    checker = make_checker(tmp_path, pi)
    result = checker.install()
    assert (result.old, result.new) == (OLD[:7], NEW[:7])
    assert pi.steps()[-2:] == ["pip", "init-db"]
    assert pi.call("pip") == [PYTHON, "-m", "pip", "install", "-r",
                              str(tmp_path / "requirements-pi.txt")]
    assert pi.timeouts["pip"] >= 900
    assert pi.call("init-db") == [PYTHON, "-m", "nfc_login.admin", "init-db"]   # no --config


def test_failed_pip_puts_the_old_version_back(tmp_path):
    pi = FakePi(changed=["requirements-pi.txt"], fail={
        "pip": (1, "Collecting foo\nERROR: No matching distribution found for foo==9\n")})
    checker = make_checker(tmp_path, pi)
    with pytest.raises(UpdateError) as err:
        checker.install()
    assert str(err.value) == ("Couldn't install the new Python packages, so the update was "
                              "undone: No matching distribution found for foo==9")
    assert pi.steps()[-2:] == ["pip", "reset"]                         # no init-db
    assert pi.call("reset")[3:] == ["reset", "--quiet", "--keep", OLD]
    assert pi.head == OLD and checker.new_commits == 3


def test_pip_hanging_is_also_undone(tmp_path):
    pi = FakePi(changed=["requirements-pi.txt"],
                fail={"pip": subprocess.TimeoutExpired("pip", 900)})
    with pytest.raises(UpdateError, match="update was undone: pip took too long"):
        make_checker(tmp_path, pi).install()
    assert pi.head == OLD


def test_failed_undo_says_so(tmp_path):
    pi = FakePi(changed=["requirements-pi.txt"], fail={
        "pip": (1, "ERROR: out of disk space"),
        "reset": (128, "error: Entry 'requirements-pi.txt' not uptodate. Cannot merge.\n"
                       "fatal: Could not reset index file to revision '1111111'.")})
    with pytest.raises(UpdateError, match=r"\(out of disk space\), and undoing the update "
                                          r"failed too: Could not reset index file"):
        make_checker(tmp_path, pi).install()


def test_merge_that_cannot_fast_forward_changes_nothing(tmp_path):
    pi = FakePi(fail={"merge": (128, "error: Your local changes to the following files would "
                                     "be overwritten by merge:\n\tnfc_login/app.py\n"
                                     "Please commit your changes or stash them before you "
                                     "merge.\nAborting\n")})
    checker = make_checker(tmp_path, pi)
    with pytest.raises(UpdateError) as err:
        checker.install()
    assert str(err.value) == ("Couldn't apply the update: Your local changes to the following "
                              "files would be overwritten by merge")
    assert pi.steps() == ["rev-parse", "fetch", "merge"]                # no reset, pip, init-db
    assert checker.new_commits == 3


def test_failed_fetch_gives_gits_error(tmp_path):
    pi = FakePi(fail={"fetch": (128, "fatal: unable to access 'https://example.com/x.git/': "
                                     "Could not resolve host: example.com")})
    with pytest.raises(UpdateError, match="^Couldn't download the update: unable to access"):
        make_checker(tmp_path, pi).install()
    assert pi.steps() == ["rev-parse", "fetch"]


def test_install_offline_or_not_a_clone(tmp_path):
    pi = FakePi()
    checker = UpdateChecker(tmp_path, run=pi, online=lambda: True)
    with pytest.raises(UpdateError, match="^This copy wasn't installed with git, so it can't "
                                          "update itself.$"):
        checker.install()
    (tmp_path / ".git").mkdir()
    checker = UpdateChecker(tmp_path, run=pi, online=lambda: False)
    with pytest.raises(UpdateError, match="^The Pi isn't online.$"):
        checker.install()
    assert pi.calls == []


def test_git_missing_is_an_update_error(tmp_path):
    pi = FakePi(fail={"rev-parse": FileNotFoundError(2, "No such file or directory")})
    with pytest.raises(UpdateError, match="Couldn't run git: No such file or directory"):
        make_checker(tmp_path, pi).install()


def test_changed_setup_scripts_add_a_note(tmp_path):
    pi = FakePi(changed=["scripts/setup-pi.sh", "nfc_login/app.py"])
    checker = make_checker(tmp_path, pi)
    assert not checker.setup_needed
    result = checker.install()
    assert result.notes == [SETUP_NOTE]
    assert "pip" not in pi.steps()
    assert checker.setup_needed             # the System menu keeps reminding


def test_app_change_without_the_control_pipe_says_to_restart_the_pi(tmp_path, monkeypatch):
    monkeypatch.delenv("NFC_KIOSK_CONTROL_FD", raising=False)
    pi = FakePi(changed=["electron/main.js"])
    assert make_checker(tmp_path, pi).install().notes == [APP_NOTE]
    monkeypatch.setenv("NFC_KIOSK_CONTROL_FD", "3")   # the app restarts itself
    assert make_checker(tmp_path, FakePi(changed=["electron/main.js"])).install().notes == []


def test_failed_database_update_puts_the_old_code_back(tmp_path):
    # The running kiosk keeps working, and the restart doesn't start new code on a
    # database it doesn't match.
    pi = FakePi(fail={"init-db": (1, "Error: (1045, \"Access denied for user 'nfc'\")")})
    with pytest.raises(UpdateError, match="^Couldn't update the database, so the update was "
                                          "undone: .*Access denied"):
        make_checker(tmp_path, pi).install()
    assert pi.steps()[-1] == "reset" and pi.head == OLD


def test_unknown_changed_files_assume_everything_changed(tmp_path):
    pi = FakePi(fail={"diff": (128, "fatal: bad object")})
    result = make_checker(tmp_path, pi).install()
    assert pi.steps()[-2:] == ["pip", "init-db"] and result.notes == [SETUP_NOTE]


def test_already_up_to_date_still_updates_the_database(tmp_path):
    pi = FakePi(changed=[])
    pi.head = NEW                                   # merge says "Already up to date."
    checker = make_checker(tmp_path, pi)
    result = checker.install()
    assert (result.old, result.new) == (NEW[:7], NEW[:7])
    assert result.notes == ["It was already up to date."] and pi.steps()[-1] == "init-db"


def test_background_check_skips_while_installing(tmp_path):
    seen = []

    class SlowPi(FakePi):
        def __call__(self, cmd, **kwargs):
            if self.step(cmd) == "fetch" and not seen:
                seen.append(checker.check())          # the background loop waking up now
            return super().__call__(cmd, **kwargs)
    checker = make_checker(tmp_path, SlowPi())
    checker.install()
    assert seen == [None]


def test_menu_check_and_available_while_installing(tmp_path):
    seen = []

    class SlowPi(FakePi):
        def __call__(self, cmd, **kwargs):
            if self.step(cmd) == "fetch" and not seen:
                seen.append(checker.available)
                with pytest.raises(UpdateError, match="being installed"):
                    checker.check(wait=True)       # the admin menu: says why, doesn't wait
                seen.append("checked")
            return super().__call__(cmd, **kwargs)
    checker = make_checker(tmp_path, SlowPi())
    checker.new_commits = 2
    checker.install()
    assert seen == [False, "checked"] and not checker.installing


# ---------------------------------------------------------------- install (real git)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def clones(tmp_path, monkeypatch):
    """A bare 'GitHub' repo, the kiosk's clone of it, and a second clone to push from."""
    for var, value in [("GIT_AUTHOR_NAME", "Test Bot"), ("GIT_COMMITTER_NAME", "Test Bot"),
                       ("GIT_AUTHOR_EMAIL", "test@example.com"),
                       ("GIT_COMMITTER_EMAIL", "test@example.com"),
                       ("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig")),
                       ("GIT_CONFIG_NOSYSTEM", "1")]:
        monkeypatch.setenv(var, value)
    (tmp_path / "gitconfig").write_text("")
    origin, dev, kiosk = tmp_path / "origin.git", tmp_path / "dev", tmp_path / "kiosk"
    git("init", "--quiet", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    git("init", "--quiet", "-b", "main", str(dev), cwd=tmp_path)
    (dev / "app.py").write_text("VERSION = 1\n")
    (dev / "requirements-pi.txt").write_text("PyMySQL>=1.1\n")
    git("add", ".", cwd=dev)
    git("commit", "--quiet", "-m", "First version", cwd=dev)
    git("remote", "add", "origin", str(origin), cwd=dev)
    git("push", "--quiet", "-u", "origin", "main", cwd=dev)
    git("clone", "--quiet", str(origin), str(kiosk), cwd=tmp_path)
    # A new version lands on "GitHub" after the kiosk was set up.
    (dev / "app.py").write_text("VERSION = 2\n")
    (dev / "requirements-pi.txt").write_text("PyMySQL>=1.1\nrequests>=2\n")
    git("commit", "--quiet", "-am", "Version two", cwd=dev)
    git("push", "--quiet", cwd=dev)
    # Stands in for the venv python: records what it was asked to run, does nothing.
    python = tmp_path / "python"
    python.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path / "python-calls"}"\n')
    python.chmod(0o755)
    return SimpleNamespace(kiosk=kiosk, dev=dev, python=str(python),
                           python_calls=tmp_path / "python-calls")


@needs_git
def test_real_install_moves_head_to_the_new_version(clones):
    kiosk = clones.kiosk
    old = git("rev-parse", "HEAD", cwd=kiosk)
    checker = UpdateChecker(kiosk, online=lambda: True, python=clones.python)
    assert checker.check() == 1
    assert checker.pending_changes() == ["Version two"]
    result = checker.install()
    new = git("rev-parse", "HEAD", cwd=kiosk)
    assert new != old and new == git("rev-parse", "HEAD", cwd=clones.dev)
    assert (result.old, result.new) == (old[:7], new[:7])
    assert (kiosk / "app.py").read_text() == "VERSION = 2\n"
    assert checker.new_commits == 0 and checker.pending_changes() == []
    assert clones.python_calls.read_text().splitlines() == [
        f"-m pip install -r {kiosk / 'requirements-pi.txt'}", "-m nfc_login.admin init-db"]


@needs_git
def test_real_install_with_local_edits_in_the_way_changes_nothing(clones):
    kiosk = clones.kiosk
    old = git("rev-parse", "HEAD", cwd=kiosk)
    (kiosk / "app.py").write_text("VERSION = 1  # tweaked on the Pi\n")
    checker = UpdateChecker(kiosk, online=lambda: True, python=clones.python)
    with pytest.raises(UpdateError, match="^Couldn't apply the update: Your local changes"):
        checker.install()
    assert git("rev-parse", "HEAD", cwd=kiosk) == old
    assert (kiosk / "app.py").read_text() == "VERSION = 1  # tweaked on the Pi\n"
    assert not clones.python_calls.exists()


@needs_git
def test_real_failed_pip_undo_keeps_local_edits(clones):
    kiosk = clones.kiosk
    old = git("rev-parse", "HEAD", cwd=kiosk)
    (kiosk / "notes.txt").write_text("not tracked\n")
    (kiosk / "python").write_text("#!/bin/sh\necho 'ERROR: no network' >&2\nexit 1\n")
    (kiosk / "python").chmod(0o755)
    checker = UpdateChecker(kiosk, online=lambda: True, python=str(kiosk / "python"))
    with pytest.raises(UpdateError, match="update was undone: no network"):
        checker.install()
    assert git("rev-parse", "HEAD", cwd=kiosk) == old
    assert (kiosk / "app.py").read_text() == "VERSION = 1\n"
    assert (kiosk / "notes.txt").read_text() == "not tracked\n"
