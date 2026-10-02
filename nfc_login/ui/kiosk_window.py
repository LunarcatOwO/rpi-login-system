"""Tkinter kiosk window sized for the 5 inch 800x480 touchscreen.

Layout:
    +-------------------------------------------------------------+
    | NFC Sign In            Season 2026              16:42:07    |
    +--------------------------------------+----------------------+
    |  Welcome, Taylor!                    | [Here now 4][Leaders]|
    |  Signed in at ...                    | A007 Taylor Robot 1h |
    |  ID A007 · Robot                     | 003  Alex  Mentor 0h |
    |  ...                                 |  ...                 |
    +--------------------------------------+----------------------+
    | > typed keypad input    No card? Keypad ID: A Robot  B Impact ... |
    +-------------------------------------------------------------+

The right-hand panel has two touch tabs: who is signed in right now (live,
refreshed every few seconds and after every scan) and the season leaderboard.

Hardware threads never touch Tk directly: they put Screens on a queue that
the Tk main loop drains.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from datetime import datetime

from nfc_login.kiosk.controller import KioskController, Screen
from nfc_login.services import timefmt

log = logging.getLogger(__name__)

COLORS = {
    "bg": "#101418",
    "panel": "#1b2129",
    "tab": "#262e38",
    "text": "#e8edf2",
    "muted": "#8a97a6",
    "info": "#4aa3ff",
    "success": "#3ccf7a",
    "warning": "#f2b33d",
    "error": "#ff5c5c",
    "prompt": "#c58cff",
}

# Computer keyboard -> keypad, for simulated mode (and handy with a USB keyboard).
KEY_MAP = {"Return": "#", "KP_Enter": "#", "BackSpace": "*", "Escape": "*"}

HERE_REFRESH_MS = 5_000
BOARD_REFRESH_MS = 60_000


class KioskWindow:
    def __init__(self, root: tk.Tk, controller: KioskController, ui_config: dict,
                 simulated_reader=None, buzzer=None):
        self.root = root
        self.controller = controller
        self.cfg = ui_config
        self.events: queue.Queue = queue.Queue()
        self.buzzer = buzzer
        self._revert_job = None
        self._keys: queue.Queue = queue.Queue()
        self._tab = "here"
        self._here: list[dict] = []
        self._board: list = []
        threading.Thread(target=self._key_loop, daemon=True, name="keys").start()

        root.title("NFC Sign In")
        root.configure(bg=COLORS["bg"])
        if ui_config["fullscreen"]:
            root.attributes("-fullscreen", True)
            root.config(cursor="none")
        else:
            root.geometry(f"{ui_config['width']}x{ui_config['height']}")

        self._build(simulated_reader)
        root.bind("<Key>", self._on_keyboard)

        self.show(controller.idle_screen())
        self._tick_clock()
        self._drain_events()
        self._check_timeout()
        self._periodic_here()
        self._periodic_board()

    # ------------------------------------------------------------ layout

    def _build(self, simulated_reader) -> None:
        big = ("DejaVu Sans", 24, "bold")
        normal = ("DejaVu Sans", 14)
        small = ("DejaVu Sans", 11)

        header = tk.Frame(self.root, bg=COLORS["panel"])
        header.pack(fill="x")
        tk.Label(header, text="NFC Sign In", font=("DejaVu Sans", 16, "bold"),
                 fg=COLORS["text"], bg=COLORS["panel"]).pack(side="left", padx=12, pady=6)
        self.clock_label = tk.Label(header, font=("DejaVu Sans", 16), fg=COLORS["text"],
                                    bg=COLORS["panel"])
        self.clock_label.pack(side="right", padx=12)
        self.season_label = tk.Label(header, font=normal, fg=COLORS["muted"], bg=COLORS["panel"])
        self.season_label.pack(side="right", padx=12)

        body = tk.Frame(self.root, bg=COLORS["bg"])
        body.pack(fill="both", expand=True)

        side = tk.Frame(body, bg=COLORS["panel"], width=300)
        side.pack(side="right", fill="y", padx=(0, 10), pady=10)
        side.pack_propagate(False)
        tabs = tk.Frame(side, bg=COLORS["panel"])
        tabs.pack(fill="x")
        self.here_tab = tk.Button(tabs, command=lambda: self._select_tab("here"))
        self.board_tab = tk.Button(tabs, text="Leaderboard",
                                   command=lambda: self._select_tab("board"))
        for button in (self.here_tab, self.board_tab):
            button.config(font=("DejaVu Sans", 13, "bold"), relief="flat", bd=0,
                          highlightthickness=0, pady=8, activebackground=COLORS["tab"],
                          activeforeground=COLORS["text"])
            button.pack(side="left", fill="x", expand=True)
        self.side_label = tk.Label(side, font=("DejaVu Sans Mono", 11), anchor="nw",
                                   justify="left", fg=COLORS["text"], bg=COLORS["panel"])
        self.side_label.pack(fill="both", expand=True, padx=10, pady=8)

        status = tk.Frame(body, bg=COLORS["bg"])
        status.pack(side="left", fill="both", expand=True, padx=16, pady=12)
        self.title_label = tk.Label(status, font=big, anchor="w", justify="left",
                                    bg=COLORS["bg"], wraplength=450)
        self.title_label.pack(fill="x")
        self.lines_label = tk.Label(status, font=normal, anchor="nw", justify="left",
                                    fg=COLORS["text"], bg=COLORS["bg"], wraplength=450)
        self.lines_label.pack(fill="both", expand=True, pady=(10, 0))

        footer = tk.Frame(self.root, bg=COLORS["panel"])
        footer.pack(fill="x")
        self.entry_label = tk.Label(footer, font=("DejaVu Sans Mono", 18, "bold"),
                                    fg=COLORS["prompt"], bg=COLORS["panel"])
        self.entry_label.pack(side="left", padx=12, pady=6)
        keys = "/".join(s["key"] for s in self.controller.users.sections if s["key"])
        hint = f"No card? Type your ID: {keys} + number"
        if self.controller.mentors:
            hint += ", mentors just the number"
        tk.Label(footer, text=f"{hint}    * admin", font=small,
                 fg=COLORS["muted"], bg=COLORS["panel"]).pack(side="right", padx=12)

        self._select_tab("here")
        if simulated_reader is not None:
            self._build_simulator(simulated_reader)

    def _build_simulator(self, reader) -> None:
        sim = tk.Frame(self.root, bg="#2a1f00")
        sim.pack(fill="x", side="bottom")
        tk.Label(sim, text="SIMULATOR  card UID:", fg="#ffd27a", bg="#2a1f00").pack(side="left",
                                                                                padx=6)
        uid_var = tk.StringVar(value="04A1B2C3D4E5F6")
        entry = tk.Entry(sim, textvariable=uid_var, width=18)
        entry.pack(side="left")
        tk.Button(sim, text="Tap card", command=lambda: reader.tap(uid_var.get())).pack(
            side="left", padx=6)
        tk.Label(sim, text="Enter=#  Bksp/Esc=*", fg="#ffd27a", bg="#2a1f00").pack(
            side="left", padx=6)
        self._sim_entry = entry

    def _select_tab(self, tab: str) -> None:
        self._tab = tab
        for name, button in (("here", self.here_tab), ("board", self.board_tab)):
            active = name == tab
            button.config(bg=COLORS["tab"] if active else COLORS["panel"],
                          fg=COLORS["text"] if active else COLORS["muted"])
        self._render_side()

    # ------------------------------------------------------------ updates

    def publish(self, screen: Screen) -> None:
        """Thread-safe: queue a screen to show."""
        self.events.put(("screen", screen))

    def show(self, screen: Screen, sound: bool = True) -> None:
        if sound and self.buzzer:
            self.buzzer.play(screen.buzz)
        self.title_label.config(text=screen.title, fg=COLORS.get(screen.tone, COLORS["text"]))
        self.lines_label.config(text="\n".join(screen.lines))
        self.entry_label.config(text=f"> {screen.entry}" if screen.entry is not None else "")
        if self._revert_job:
            self.root.after_cancel(self._revert_job)
            self._revert_job = None
        if screen.hold_seconds:
            self._revert_job = self.root.after(int(screen.hold_seconds * 1000), self._revert)
        if screen.refresh_leaderboard:
            self.refresh(board=True)

    def _revert(self) -> None:
        self._revert_job = None
        if self.controller.state == "idle":
            self.show(self.controller.idle_screen(), sound=False)

    def refresh(self, board: bool = False) -> None:
        """Fetch who's here (and optionally the leaderboard) off the UI thread."""
        def work():
            try:
                attendance = self.controller.attendance
                here = attendance.currently_signed_in()
                self.events.put(("here", here))
                if board:
                    season = attendance.active_season_name()
                    entries = attendance.leaderboard(self.cfg["leaderboard_size"])
                    self.events.put(("board", (season, entries)))
            except Exception:
                log.exception("refresh failed")
        threading.Thread(target=work, daemon=True).start()

    def _render_side(self) -> None:
        self.here_tab.config(text=f"Here now ({len(self._here)})")
        if self._tab == "here":
            now = datetime.now()
            rows = []
            for r in self._here:
                elapsed = timefmt.format_duration(int((now - r["sign_in_at"]).total_seconds()))
                rows.append(f"{r['code']:<4} {r['username'][:8]:<8} {self._team(r['section'])} "
                            f"{elapsed:>7}")
            self.side_label.config(text="\n".join(rows) or "Nobody signed in yet")
        else:
            # 31 characters: what fits the 300 px panel at this font size.
            rows = [f"{e.rank:>2} {e.code:<4} {e.username[:6]:<6} {self._team(e.section)} "
                    f"{timefmt.format_duration(e.total_seconds):>7}" for e in self._board]
            self.side_label.config(text="\n".join(rows) or "No one yet")

    def _team(self, section: str) -> str:
        return f"{self.controller.users.team_name(section, short=True)[:8]:<8}"

    # ------------------------------------------------------------ loops

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "screen":
                    self.show(payload)
                elif kind == "here":
                    self._here = payload
                    self._render_side()
                elif kind == "board":
                    season, self._board = payload
                    self.season_label.config(text=f"Season {season}")
                    self._render_side()
        except queue.Empty:
            pass
        self.root.after(50, self._drain_events)

    def _tick_clock(self) -> None:
        self.clock_label.config(text=datetime.now().strftime("%H:%M:%S"))
        self.root.after(1000, self._tick_clock)

    def _check_timeout(self) -> None:
        screen = self.controller.check_timeout()
        if screen:
            self.show(screen)
        self.root.after(1000, self._check_timeout)

    def _periodic_here(self) -> None:
        # Live list: picks up sign-ins from the keypad, web admin and clean-up jobs too.
        self.refresh()
        self.root.after(HERE_REFRESH_MS, self._periodic_here)

    def _periodic_board(self) -> None:
        self.refresh(board=True)
        self.root.after(BOARD_REFRESH_MS, self._periodic_board)

    # ------------------------------------------------------------ input

    def press(self, key: str) -> None:
        """Queue one keypad key. Safe to call from any thread."""
        if self.buzzer:
            self.buzzer.play("key")
        self._keys.put(key)

    def _key_loop(self) -> None:
        # One worker so keys are handled in order, off the UI thread (PIN
        # checks and database calls would otherwise freeze the screen).
        while True:
            key = self._keys.get()
            try:
                screen = self.controller.handle_key(key)
            except Exception as exc:
                log.exception("key %s failed", key)
                screen = Screen("Something went wrong", [str(exc)], "error", hold_seconds=6)
            if screen:
                self.publish(screen)

    def _on_keyboard(self, event) -> None:
        if getattr(self, "_sim_entry", None) is not None and event.widget is self._sim_entry:
            return
        key = KEY_MAP.get(event.keysym) or event.char.upper()
        # Typing a section letter (e.g. E) presses that section's keypad key (#).
        for section in self.controller.users.sections:
            if key == section["letter"] and section["key"]:
                key = section["key"]
        if len(key) == 1 and key in "0123456789ABCD*#":
            self.press(key)
