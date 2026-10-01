"""Tkinter kiosk window sized for the 5 inch 800x480 touchscreen.

Layout:
    +-------------------------------------------------------------+
    | NFC Sign In            Season 2026              16:42:07    |
    +--------------------------------------+----------------------+
    |  Welcome, Taylor!                      |  Leaderboard         |
    |  Signed in at ...                    |  1  Alex   40h 10m   |
    |  Season 2026 total: 12h 30m          |  2  Taylor   12h 30m   |
    |  ...                                 |  ...                 |
    +--------------------------------------+----------------------+
    | > typed keypad input           A admin  B ID+PIN  C my time |
    +-------------------------------------------------------------+

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
    "text": "#e8edf2",
    "muted": "#8a97a6",
    "info": "#4aa3ff",
    "success": "#3ccf7a",
    "warning": "#f2b33d",
    "error": "#ff5c5c",
    "prompt": "#c58cff",
}

# Computer keyboard -> keypad, for simulated mode (and handy with a USB keyboard).
KEY_MAP = {"Return": "#", "KP_Enter": "#", "BackSpace": "*", "Escape": "D"}


class KioskWindow:
    def __init__(self, root: tk.Tk, controller: KioskController, ui_config: dict,
                 simulated_reader=None):
        self.root = root
        self.controller = controller
        self.cfg = ui_config
        self.events: queue.Queue = queue.Queue()
        self._revert_job = None
        self._keys: queue.Queue = queue.Queue()
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
        self.refresh_leaderboard()
        self._tick_clock()
        self._drain_events()
        self._check_timeout()
        self._periodic_leaderboard()

    # ------------------------------------------------------------ layout

    def _build(self, simulated_reader) -> None:
        big = ("DejaVu Sans", 26, "bold")
        normal = ("DejaVu Sans", 15)
        small = ("DejaVu Sans", 12)

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

        status = tk.Frame(body, bg=COLORS["bg"])
        status.pack(side="left", fill="both", expand=True, padx=16, pady=12)
        self.title_label = tk.Label(status, font=big, anchor="w", justify="left",
                                    bg=COLORS["bg"], wraplength=470)
        self.title_label.pack(fill="x")
        self.lines_label = tk.Label(status, font=normal, anchor="nw", justify="left",
                                    fg=COLORS["text"], bg=COLORS["bg"], wraplength=470)
        self.lines_label.pack(fill="both", expand=True, pady=(10, 0))

        board = tk.Frame(body, bg=COLORS["panel"], width=280)
        board.pack(side="right", fill="y", padx=(0, 12), pady=12)
        board.pack_propagate(False)
        tk.Label(board, text="Leaderboard", font=("DejaVu Sans", 15, "bold"),
                 fg=COLORS["text"], bg=COLORS["panel"]).pack(anchor="w", padx=10, pady=(8, 4))
        self.board_label = tk.Label(board, font=("DejaVu Sans Mono", 12), anchor="nw",
                                    justify="left", fg=COLORS["text"], bg=COLORS["panel"])
        self.board_label.pack(fill="both", expand=True, padx=10)

        footer = tk.Frame(self.root, bg=COLORS["panel"])
        footer.pack(fill="x")
        self.entry_label = tk.Label(footer, font=("DejaVu Sans Mono", 16), fg=COLORS["prompt"],
                                    bg=COLORS["panel"])
        self.entry_label.pack(side="left", padx=12, pady=6)
        tk.Label(footer, text="A admin   B ID + PIN   C my time   D cancel", font=small,
                 fg=COLORS["muted"], bg=COLORS["panel"]).pack(side="right", padx=12)

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
        tk.Label(sim, text="Enter=#  Bksp=*  Esc=D",
                 fg="#ffd27a", bg="#2a1f00").pack(side="left", padx=6)
        self._sim_entry = entry

    # ------------------------------------------------------------ updates

    def publish(self, screen: Screen) -> None:
        """Thread-safe: queue a screen to show."""
        self.events.put(("screen", screen))

    def show(self, screen: Screen) -> None:
        self.title_label.config(text=screen.title, fg=COLORS.get(screen.tone, COLORS["text"]))
        self.lines_label.config(text="\n".join(screen.lines))
        self.entry_label.config(text=f"> {screen.entry}" if screen.entry is not None else "")
        if self._revert_job:
            self.root.after_cancel(self._revert_job)
            self._revert_job = None
        if screen.hold_seconds:
            self._revert_job = self.root.after(int(screen.hold_seconds * 1000), self._revert)
        if screen.refresh_leaderboard:
            self.refresh_leaderboard()

    def _revert(self) -> None:
        self._revert_job = None
        if self.controller.state == "idle":
            self.show(self.controller.idle_screen())

    def refresh_leaderboard(self) -> None:
        """Fetch the leaderboard off the UI thread, then display it."""
        def work():
            try:
                attendance = self.controller.attendance
                season = attendance.active_season_name()
                entries = attendance.leaderboard(self.cfg["leaderboard_size"])
                self.events.put(("leaderboard", (season, entries)))
            except Exception:
                log.exception("leaderboard refresh failed")
        threading.Thread(target=work, daemon=True).start()

    def _show_leaderboard(self, season: str, entries) -> None:
        self.season_label.config(text=f"Season {season}")
        rows = [f"{e.rank:>2}  {e.username[:12]:<12} {timefmt.format_duration(e.total_seconds):>8}"
                for e in entries]
        self.board_label.config(text="\n".join(rows) or "No one yet")

    # ------------------------------------------------------------ loops

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "screen":
                    self.show(payload)
                elif kind == "leaderboard":
                    self._show_leaderboard(*payload)
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

    def _periodic_leaderboard(self) -> None:
        self.refresh_leaderboard()
        self.root.after(60_000, self._periodic_leaderboard)

    # ------------------------------------------------------------ input

    def press(self, key: str) -> None:
        """Queue one keypad key. Safe to call from any thread."""
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
        if key and key in "0123456789ABCD*#" and len(key) == 1:
            self.press(key)
