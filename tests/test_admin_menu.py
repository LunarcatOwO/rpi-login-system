"""The kiosk admin menu's submenus: People, Hours and System."""

from __future__ import annotations

from nfc_login.hardware.simulated import SimulatedReader
from nfc_login.kiosk import controller as kc
from nfc_login.services.system import SystemActionError, WifiNetwork, WifiOffError
from nfc_login.services.updates import InstallResult, UpdateError


class Jobs:
    """Background jobs run only when the test says so."""

    def __init__(self):
        self.pending = []

    def __call__(self, fn):
        self.pending.append(fn)

    def run(self):
        while self.pending:
            self.pending.pop(0)()


class FakeSystem:
    def __init__(self):
        self.networks = [WifiNetwork("School Net", 80, True, False),
                         WifiNetwork("Guest", 40, False, False)]
        self.scan_error = None
        self.radio_off = False
        self.connect_error = None
        self.power_error = None
        self.connected = []
        self.powered = []

    def version(self):
        return "abc1234 (2 Oct 2026)"

    def info_lines(self):
        return ["Host: kiosk", "IP: 10.0.0.5"]

    def wifi_networks(self):
        if self.radio_off:
            raise WifiOffError("Wi-Fi is turned off.")
        if self.scan_error:
            raise SystemActionError(self.scan_error)
        return self.networks

    def wifi_on(self):
        self.radio_off = False

    def wifi_connect(self, ssid, password):
        self.connected.append((ssid, password))
        if self.connect_error:
            raise SystemActionError(self.connect_error)
        return "10.0.0.9"

    def power(self, action):
        self.powered.append(action)
        if self.power_error:
            raise SystemActionError(self.power_error)


class FakeUpdates:
    def __init__(self, found=3):
        self.found = found
        self.new_commits = 0
        self.checks = 0
        self.installs = 0
        self.install_error = None
        self.installing = False
        self.automatic = True
        self.setup_needed = False

    @property
    def available(self):
        return self.new_commits > 0 and not self.installing

    def check(self, wait=False):
        if self.installing:
            if wait:
                raise UpdateError("An update is being installed.")
            return None
        self.checks += 1
        if self.found is not None:
            self.new_commits = self.found
        return self.found

    def pending_changes(self, limit=5):
        return ["Fix the buzzer", "Add Wi-Fi setup"][:limit]

    def install(self):
        self.installs += 1
        if self.install_error:
            raise UpdateError(self.install_error)
        self.new_commits = 0
        return InstallResult("abc1234", "def5678", [])


def make(services, updates=None):
    attendance, seasons, users = services
    jobs = Jobs()
    reader = SimulatedReader()
    system = FakeSystem()
    controller = kc.KioskController(attendance, users, reader=reader, seasons=seasons,
                                    system=system, updates=updates or FakeUpdates(),
                                    spawn=jobs, sleep=lambda _s: None)
    published = []
    controller.publish = published.append
    users.set_admin_pin("2468")
    return controller, users, jobs, published, system


def press(controller, keys):
    screen = None
    for key in keys:
        screen = controller.handle_key(key) or screen
    return screen


def type_text(controller, text):
    screen = None
    for char in text:
        screen = controller.handle_char(char)
    return screen


def test_menus_and_back(services):
    controller, _users, _jobs, _published, _system = make(services)
    screen = press(controller, "*2468#")
    assert screen.title == "Admin menu" and screen.lines[0].startswith("1  People")
    assert press(controller, "1").title == "People"
    assert press(controller, "*").title == "Admin menu"
    assert press(controller, "2").title == "Hours and sign-ins"
    assert press(controller, "*3").title == "System"
    assert "2  Check for updates" in controller._redraw().lines
    press(controller, "**")
    assert controller.state == kc.IDLE


def test_update_found_installs_and_restarts(services):
    controller, _users, jobs, published, _system = make(services)
    screen = press(controller, "*2468#32")
    assert screen.title == "Checking for updates…" and controller.state == kc.BUSY
    assert press(controller, "1*#") is None                  # keys wait for the job
    controller.keypad_timeout = -1
    assert controller.check_timeout() is None                # and so does the timeout
    jobs.run()
    screen = published.pop()
    assert controller.state == kc.UPDATE_CONFIRM
    assert screen.title == "Update: 3 new changes" and "•  Fix the buzzer" in screen.lines
    controller.keypad_timeout = 30
    # The menus now point at the update.
    assert any("Update available" in line for line in press(controller, "**").lines)
    assert "2  Install update (3 new)" in press(controller, "3").lines
    press(controller, "2")
    jobs.run()
    screen = press(controller, "#")
    assert screen.title == "Installing the update…" and controller.state == kc.BUSY
    jobs.run()
    screen = published.pop()
    assert screen.restart_app and "Version abc1234 → def5678" in screen.lines
    assert controller.state == kc.IDLE


def test_update_errors_and_up_to_date(services):
    updates = FakeUpdates(found=None)
    controller, _users, jobs, published, _system = make(services, updates)
    press(controller, "*2468#32")
    jobs.run()
    assert published.pop().title == "Couldn't check for updates"
    updates.found = 0
    press(controller, "*2468#32")
    jobs.run()
    screen = published.pop()
    assert screen.title == "Up to date" and "Version abc1234 (2 Oct 2026)" in screen.lines
    updates.found, updates.install_error = 2, "Couldn't apply the update: local changes"
    press(controller, "*2468#32")
    jobs.run()
    press(controller, "#")
    jobs.run()
    screen = published.pop()
    assert screen.title == "Update not installed" and not screen.restart_app


def test_card_tap_during_a_job(services):
    controller, users, jobs, published, _system = make(services)
    users.enroll_tag("04AA", users.add("Taylor", "A")["id"])
    press(controller, "*2468#32")
    assert controller.handle_card("04AA").title == "Welcome, Taylor!"   # cards always work
    jobs.run()
    assert published == [] and controller.state == kc.IDLE   # the check's menu is dropped
    controller.updates.found = 2
    press(controller, "*2468#32")
    jobs.run()
    press(controller, "#")
    controller.handle_card("04AA")
    jobs.run()
    assert published[-1].restart_app        # an installed update still restarts


def test_wifi_password_and_connect(services):
    controller, _users, jobs, published, system = make(services)
    screen = press(controller, "*2468#33")
    assert screen.title == "Looking for Wi-Fi…"
    jobs.run()
    screen = published.pop()
    assert screen.title == "Wi-Fi networks"
    assert screen.lines[:2] == ["1  School Net  80%  locked", "2  Guest  40%"]
    screen = press(controller, "1")
    assert screen.title == "Password for School Net" and screen.keyboard == "text"
    screen = type_text(controller, "Pa$$ w")
    assert screen.entry == "•••••w_"                          # only the last one shows
    press(controller, "42")                                   # keypad digits type too
    type_text(controller, "\bx")
    screen = press(controller, "#")
    assert screen.title == "Connecting to School Net…" and controller.buffer == ""
    jobs.run()
    assert system.connected == [("School Net", "Pa$$ w4x")]
    screen = published.pop()
    assert screen.title == "Wi-Fi connected" and "IP: 10.0.0.9" in screen.lines
    assert controller.updates.checks == 1                     # online now: check for updates
    controller.updates.automatic = False                      # [updates] turned off
    press(controller, "*2468#33")
    jobs.run()
    press(controller, "2")
    jobs.run()
    assert controller.updates.checks == 1 and published.pop().title == "Wi-Fi connected"


def test_wifi_wrong_password_and_open_network(services):
    controller, _users, jobs, published, system = make(services)
    press(controller, "*2468#33")
    jobs.run()
    system.connect_error = "Wrong Wi-Fi password, or the network refused it."
    press(controller, "1")
    type_text(controller, "nope\n")
    jobs.run()
    screen = published.pop()
    assert screen.title == "Wi-Fi networks" and screen.tone == "error"
    assert screen.lines[0] == system.connect_error and controller.state == kc.WIFI_LIST
    system.connect_error = None
    press(controller, "2")                                    # open: no password asked
    jobs.run()
    assert system.connected[-1] == ("Guest", None)
    assert published.pop().title == "Wi-Fi connected"
    system.scan_error = "Wi-Fi setup needs NetworkManager (nmcli), which this Pi doesn't have."
    press(controller, "*2468#33")
    jobs.run()
    assert published.pop().lines == [system.scan_error]


def test_power_and_app_restart(services):
    controller, _users, jobs, published, system = make(services)
    screen = press(controller, "*2468#38")
    assert screen.title == "Shut down the Pi?"
    assert press(controller, "*").title == "System"
    screen = press(controller, "8#")
    assert screen.title == "Shutting down"
    jobs.run()
    assert system.powered == ["poweroff"] and published == []
    system.power_error = "Couldn't restart the Pi: not allowed"
    press(controller, "*2468#37#")
    jobs.run()
    assert published.pop().lines == [system.power_error]
    screen = press(controller, "*2468#35#")
    assert screen.restart_app and controller.state == kc.IDLE


def test_system_info(services):
    controller, _users, _jobs, _published, _system = make(services)
    controller.updates.new_commits = 2
    screen = press(controller, "*2468#31")
    assert screen.lines[:3] == ["Host: kiosk", "IP: 10.0.0.5", "NFC: simulated reader"]
    assert "Update: 2 new change(s), System > 2" in screen.lines
    assert press(controller, "*").title == "System"


def test_change_the_admin_pin(services):
    controller, users, _jobs, _published, _system = make(services)
    press(controller, "*2468#34")
    assert press(controller, "12#").lines[0] == "A PIN needs 4 to 8 digits."
    assert press(controller, "1357#").title == "Type the PIN again"
    screen = press(controller, "1358#")
    assert screen.lines[0] == "The two PINs didn't match. Try again."
    assert controller.state == kc.ADMINPIN_NEW
    assert press(controller, "1357#1357#").title == "Admin PIN changed"
    assert users.check_admin_pin("1357") and not users.check_admin_pin("2468")


def test_sign_someone_in_and_everyone_out(services, clock):
    controller, users, _jobs, _published, _system = make(services)
    users.add("Taylor", "B")
    assert press(controller, "*2468#22B001").title == "Welcome, Taylor!"
    screen = press(controller, "*2468#24")
    assert screen.title == "Sign everyone out?" and "1 signed in right now." in screen.lines[0]
    clock.advance(minutes=30)
    assert press(controller, "#").title == "Everyone signed out"
    assert controller.attendance.currently_signed_in() == []


def test_remove_someones_cards(services):
    controller, users, _jobs, _published, _system = make(services)
    user = users.add("Taylor", "C")
    press(controller, "*2468#13C001")
    assert controller.state == kc.UNCARD_ID                    # no cards: say so, stay
    assert controller._redraw().title == "Remove cards: user ID"
    users.enroll_tag("04AA", user["id"])
    users.enroll_tag("04BB", user["id"])
    press(controller, "*")
    screen = press(controller, "3C001")
    assert screen.title == "Remove Taylor's cards?" and "   04AA" in screen.lines
    assert press(controller, "#").title == "Cards removed"
    assert controller.handle_card("04AA").title == "Not signed in"


def test_rename_and_set_a_pin(services, clock):
    controller, users, _jobs, _published, _system = make(services)
    users.add("Taylor", "A")
    users.add("Sam Lee", "A")
    screen = press(controller, "*2468#15A001")
    assert screen.entry == "Taylor_" and screen.keyboard == "name"
    screen = type_text(controller, " jones\n")
    assert screen.title == "Renamed" and "Taylor is now Taylor Jones (A001)." in screen.lines
    press(controller, "*2468#15A002")
    type_text(controller, "\b" * 7 + "taylor jones\n")
    assert controller.state == kc.RENAME_NAME                  # name taken: keep typing

    press(controller, "*" * 13)                                # delete it all, back
    press(controller, "**")
    assert controller.state == kc.ADMIN_MENU
    screen = press(controller, "16A001")
    assert screen.title == "PIN for Taylor Jones (A001)"
    assert press(controller, "4321#4321#").title == "PIN set"
    assert press(controller, "A00114321#").title == "Welcome, Taylor Jones!"
    screen = press(controller, "*2468#16A001")
    assert "D  Remove their PIN" in screen.lines
    # Removing it asks first, whether by D or by # with nothing typed.
    assert press(controller, "#").title == "Remove Taylor Jones's PIN?"
    assert press(controller, "*").title == "People"
    assert users.get_by_code("A001")["pin_hash"] is not None
    assert press(controller, "6A001D").title == "Remove Taylor Jones's PIN?"
    assert press(controller, "#").title == "PIN removed"
    assert users.get_by_code("A001")["pin_hash"] is None
    assert "D  Remove their PIN" not in press(controller, "*2468#16A001").lines
    assert press(controller, "D").title == "PIN for Taylor Jones (A001)"   # no PIN: D does nothing


def test_rename_unchanged_and_long_names(services):
    controller, users, _jobs, _published, _system = make(services)
    long_name = "Alexandra Catherine Montgomery-Fitzgerald III"     # 45 letters
    users.add(long_name, "B")
    users.add("Robin", "A")
    screen = press(controller, "*2468#15A001")
    screen = type_text(controller, "\n")
    assert screen.title == "Name not changed"
    screen = press(controller, "*2468#15B001")
    assert screen.entry == long_name[:kc.MAX_NAME] + "_"
    assert "shortened" in screen.lines[0]
    assert type_text(controller, "\n").title == "Name not changed"     # not cut short
    assert users.get_by_code("B001")["username"] == long_name


def test_nothing_else_while_an_update_installs(services):
    controller, users, jobs, published, _system = make(services)
    press(controller, "*2468#32")
    jobs.run()
    press(controller, "#")                     # install: runs in the background
    controller.updates.installing = True
    assert "Installing an update" in controller.idle_screen().lines[0]
    users.enroll_tag("04AA", users.add("Taylor", "A")["id"])
    controller.handle_card("04AA")             # a card cancels the menu, not the install
    for key in "235678":
        screen = press(controller, "*2468#3" + key)
        assert screen.tone == "error" and "being installed" in screen.lines[0], key
        assert controller.state == kc.SYSTEM_MENU
        press(controller, "**")
    assert press(controller, "*2468#31").title == "System info"   # info still works
    press(controller, "***")
    controller.updates.installing = False
    jobs.run()
    assert published[-1].restart_app


def test_a_slow_job_doesnt_eat_the_keypad_timeout(services):
    now = [0.0]
    controller, _users, jobs, published, _system = make(services)
    controller.monotonic = lambda: now[0]
    press(controller, "*2468#33")
    now[0] = 25                                # the scan took 25 s of the 30 s timeout
    jobs.run()
    assert controller.state == kc.WIFI_LIST
    now[0] = 40
    assert controller.check_timeout() is None  # 15 s since the list showed
    now[0] = 56
    assert controller.check_timeout().title == "Tap your card"


def test_setup_reminder_after_an_update(services):
    controller, _users, _jobs, _published, _system = make(services)
    controller.updates.setup_needed = True
    assert press(controller, "*2468#3").lines[0] == kc.SETUP_REMINDER
    assert kc.SETUP_REMINDER in press(controller, "1").lines


def test_deactivate_and_reactivate(services, clock):
    controller, users, _jobs, _published, _system = make(services)
    users.enroll_tag("04AA", users.add("Taylor", "D")["id"])
    controller.handle_card("04AA")
    clock.advance(minutes=20)
    screen = press(controller, "*2468#17D001")
    assert screen.title == "Deactivate Taylor?"
    assert press(controller, "#").title == "Taylor deactivated"
    assert controller.attendance.currently_signed_in() == []   # signed out first
    assert controller.handle_card("04AA").tone == "error"
    assert press(controller, "D001").lines[0] == "D001 is deactivated."
    press(controller, "*")
    screen = press(controller, "*2468#17D001")
    assert screen.title == "Reactivate Taylor?"
    assert press(controller, "#").title == "Taylor reactivated"
    clock.advance(minutes=1)
    assert controller.handle_card("04AA").title == "Welcome, Taylor!"


def test_start_a_new_season(services, clock):
    controller, users, _jobs, _published, _system = make(services)
    users.enroll_tag("04AA", users.add("Taylor", "B")["id"])
    controller.handle_card("04AA")
    clock.advance(hours=2)
    controller.seasons.ensure_active()
    current = controller.attendance.active_season_name()
    new = controller.seasons.next_name()
    screen = press(controller, "*2468#25")
    assert screen.title == f"Start season {new}?" and screen.tone == "warning"
    assert press(controller, "1111#").title == "Wrong PIN"     # nothing happens
    assert controller.attendance.active_season_name() == current
    screen = press(controller, "*2468#252468#")
    assert screen.title == "New season started"
    assert controller.attendance.active_season_name() == new
    assert controller.attendance.currently_signed_in() == []
    stats = controller.attendance.user_stats(users.get_by_code("B001")["id"])
    assert stats.total_text.startswith("0h")


def test_wifi_switched_off_is_switched_on(services):
    controller, _users, jobs, published, system = make(services)
    system.radio_off = True
    press(controller, "*2468#33")
    jobs.run()
    assert published.pop().title == "Wi-Fi networks" and not system.radio_off


def test_touch_and_keypad_lines(services):
    """Every key line follows the "K  label" form the windows turn into buttons."""
    controller, users, _jobs, _published, _system = make(services)
    idle = controller.idle_screen()
    assert "A  Robot      B  Impact" in idle.lines and "*  Admin menu" in idle.lines
    assert "#  Type your ID on the screen" in idle.lines
    assert idle.lines[1].endswith("Mentors: numbers only.")
    screen = press(controller, "#")                           # # on the start screen: type an ID
    assert controller.state == kc.USER_ID and screen.keyboard == "keypad"
    assert press(controller, "*").title == "Tap your card"
    assert press(controller, "*").keyboard == "keypad"        # admin PIN
    assert press(controller, "2468#1").keyboard is None       # menus are buttons already
    screen = press(controller, "1")
    assert screen.keyboard is None and screen.lines[-1] == "*  back"
    screen = press(controller, "1")
    assert screen.keyboard == "name"
    assert press(controller, "5").entry == "_"                # the keypad doesn't type names


def test_who_is_here_pages(services, clock):
    controller, users, _jobs, _published, _system = make(services)
    for n in range(19):
        users.enroll_tag(f"04{n:02d}", users.add(f"Person {n}", "A")["id"])
        controller.handle_card(f"04{n:02d}")
    screen = press(controller, "*2468#23")
    assert screen.title == "Here now: 19" and len(screen.lines) == 10
    assert screen.lines[-2] == "Page 1 of 3" and screen.lines[-1] == "#  next page      *  back"
    assert press(controller, "##").lines[-2] == "Page 3 of 3"
    assert len(controller._redraw().lines) == 3 + 2
    assert press(controller, "#").lines[-2] == "Page 1 of 3"            # round again
    assert press(controller, "*").title == "Hours and sign-ins"
