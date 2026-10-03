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
A list too long for the panel shows a page at a time and turns its pages by
itself; a name too long for its column slides sideways to show its end.
While a name or Wi-Fi password is being typed, an on-screen keyboard takes
the side panel's place; while an ID, PIN or amount is, an on-screen 4x4 keypad
does.

Everything works by touch as well as on the keypad: a screen line of "K  label"
parts (see Screen) is drawn as buttons that press those keys.

Hardware threads never touch Tk directly: they put Screens on a queue that
the Tk main loop drains.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime

from nfc_login.kiosk.controller import KioskController, Screen
from nfc_login.services import timefmt
from nfc_login.ui.screen_keys import KEYPAD, KEYPAD_CAPTIONS, blocks, menu_columns

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

# On-screen keyboards, as rows of keys. "name" types people's names (letters go
# in lower case; the controller capitalises them). "text" is a full keyboard for
# Wi-Fi passwords: a one-shot Shift, and a 123 page of digits and symbols.
# One-character keys type themselves, "" is a half-key gap, longer labels are
# special keys. Each row is spread across the full width by KEY_WEIGHTS.
NAME_KEYS = [list("QWERTYUIOP"), list("ASDFGHJKL'"), list("ZXCVBNM-."),
             ["Space", "Delete", "Done"]]
TEXT_KEYS = {
    "letters": [list("qwertyuiop"), ["", *"asdfghjkl", ""], ["Shift", *"zxcvbnm", "Delete"],
                ["123", "Space", "Done"]],
    "symbols": [list("1234567890"), list("-/:;()$&@\""), list(".,?!'#%*+="),
                list("_\\|~<>{}[]^`"), ["ABC", "Space", "Delete", "Done"]],
}
KEY_WEIGHTS = {"": 1, "Shift": 3, "Delete": 3, "123": 4, "ABC": 4, "Done": 4, "Space": 8}
KEY_CHARS = {"Space": " ", "Delete": "\b", "Done": "\n"}
KEY_HEIGHT = 46

HERE_REFRESH_MS = 5_000
BOARD_REFRESH_MS = 60_000

SIDE_WIDTH = 340            # the right-hand panel
STATUS_WRAP = 416           # the status text beside it: 800 - 350 (panel) - 2 x 16
SIDE_TICK_MS = 250          # how often the panel's moving parts are redrawn
PAGE_MS = 6_000             # a long list turns a page this often (or once its names have slid)
MARQUEE_HOLD_MS = 2_000     # a name too long for its column rests at its start and its end
MARQUEE_STEP_MS = 400       # and slides a character at a time in between: 2.5 a second


class KioskWindow:
    def __init__(self, root: tk.Tk, controller: KioskController, ui_config: dict,
                 simulated_reader=None, buzzer=None, updates=None):
        self.root = root
        self.updates = updates
        self.controller = controller
        self.cfg = ui_config
        self.events: queue.Queue = queue.Queue()
        self.buzzer = buzzer
        self._revert_job = None
        self._keys: queue.Queue = queue.Queue()
        self._tab = "here"
        self._here: list[dict] = []
        self._board: list = []
        self._page = 0                      # side panel: the page of a long list showing,
        self._pages = 1                     # how many there are,
        self._page_ms = PAGE_MS             # how long this one stays,
        self._page_at = time.monotonic()    # and when it (and its sliding names) started
        self._side_size = (0, 0)            # the list's size in px, once laid out
        self._kb_mode: str | None = None    # the keyboard showing: None, "name" or "text"
        self._kb_page = "letters"           # "text" keyboard: "letters" or "symbols"
        self._kb_shift = False              # next letter in upper case
        self.restart_requested = False      # __main__ starts the app again after mainloop
        # Background jobs (Wi-Fi, updates) show their result through this.
        controller.publish = self.publish
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
        self._tick_side()

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
        # Shown (by the clock) only when a newer version is on GitHub.
        self.update_label = tk.Label(header, text="\u2b07 Update", font=("DejaVu Sans", 12),
                                     fg=COLORS["info"], bg=COLORS["panel"])
        self.season_label = tk.Label(header, font=normal, fg=COLORS["muted"], bg=COLORS["panel"])
        self.season_label.pack(side="right", padx=12)

        # The footer is packed before the body, so the body gives way, never the footer.
        footer = tk.Frame(self.root, bg=COLORS["panel"])
        footer.pack(fill="x", side="bottom")
        self.footer = footer
        # 22 px, as on the web page, so the footer leaves the body room for its buttons.
        self.entry_label = tk.Label(footer, font=("DejaVu Sans Mono", -22, "bold"),
                                    fg=COLORS["prompt"], bg=COLORS["panel"])
        self.entry_label.pack(side="left", padx=12, pady=4)
        keys = "/".join(s["key"] for s in self.controller.users.sections if s["key"])
        hint = f"No card? Type your ID: {keys} + number"
        if self.controller.mentors:
            hint += ", mentors numbers only"
        tk.Label(footer, text=f"{hint}    * admin", font=small,
                 fg=COLORS["muted"], bg=COLORS["panel"]).pack(side="right", padx=12)

        body = tk.Frame(self.root, bg=COLORS["bg"])
        body.pack(fill="both", expand=True)

        side = tk.Frame(body, bg=COLORS["panel"], width=SIDE_WIDTH)
        side.pack(side="right", fill="y", padx=(0, 10), pady=10)
        side.pack_propagate(False)
        self.side = side
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
        # Monospace, so the columns line up; how many characters and lines fit is
        # measured once it is laid out (_side_resized).
        self.side_font = tkfont.Font(root=self.root, family="DejaVu Sans Mono", size=10)
        self.page_font = tkfont.Font(root=self.root, family="DejaVu Sans", size=9)
        self.side_label = tk.Label(side, font=self.side_font, anchor="nw", justify="left",
                                   fg=COLORS["text"], bg=COLORS["panel"], bd=0, padx=0, pady=0)
        self.side_label.pack(fill="both", expand=True, padx=10, pady=8)
        self.side_label.bind("<Configure>", self._side_resized)
        # "2/5" in the corner while a long list shows a page at a time.
        self.page_label = tk.Label(side, font=self.page_font, fg=COLORS["muted"],
                                   bg=COLORS["panel"], bd=0, padx=0, pady=0)

        status = tk.Frame(body, bg=COLORS["bg"])
        self.status_frame = status
        status.pack(side="left", fill="both", expand=True, padx=16, pady=(10, 6))
        self.title_label = tk.Label(status, font=big, anchor="w", justify="left",
                                    bg=COLORS["bg"], wraplength=STATUS_WRAP)
        self.title_label.pack(fill="x")
        # The screen's lines: text, and buttons for its "K  label" lines (_draw_lines).
        self.lines_frame = tk.Frame(status, bg=COLORS["bg"])
        self.lines_frame.pack(fill="both", expand=True, pady=(8, 0))
        self._lines_drawn: tuple | None = None
        self.keyboard = tk.Frame(status, bg=COLORS["bg"])

        # The 4x4 keypad on screen (keyboard="keypad"), in the side panel's place.
        self.keypad = tk.Frame(body, bg=COLORS["panel"], width=SIDE_WIDTH)
        self.keypad.pack_propagate(False)
        self.keypad.grid_propagate(False)
        for i in range(4):
            self.keypad.grid_rowconfigure(i, weight=1, uniform="row")
            self.keypad.grid_columnconfigure(i, weight=1, uniform="col")
        for r, row in enumerate(KEYPAD):
            for c, key in enumerate(row):
                self._keypad_key(key).grid(row=r, column=c, sticky="nsew", padx=3, pady=3)

        self._select_tab("here")
        if simulated_reader is not None:
            self._build_simulator(simulated_reader)

    def _draw_keys(self) -> None:
        """(Re)build the keyboard's buttons for its mode, page and shift."""
        for child in self.keyboard.winfo_children():
            child.destroy()
        rows = NAME_KEYS if self._kb_mode == "name" else TEXT_KEYS[self._kb_page]
        for row in rows:
            line = tk.Frame(self.keyboard, bg=COLORS["bg"])
            line.pack(fill="x", pady=2)
            line.grid_rowconfigure(0, minsize=KEY_HEIGHT)
            for col, key in enumerate(row):
                line.grid_columnconfigure(col, weight=KEY_WEIGHTS.get(key, 2), uniform="key")
                if not key:
                    tk.Frame(line, bg=COLORS["bg"]).grid(row=0, column=col)
                    continue
                bg, fg = COLORS["tab"], COLORS["text"]
                if key == "Done":
                    bg, fg = COLORS["success"], COLORS["bg"]
                elif key == "Shift" and self._kb_shift:
                    bg, fg = COLORS["info"], COLORS["bg"]
                label = key.upper() if self._kb_shift and len(key) == 1 else key
                tk.Button(line, text=label, width=1, font=("DejaVu Sans", 16, "bold"),
                          relief="flat", bd=0, highlightthickness=0, bg=bg, fg=fg,
                          activebackground=COLORS["panel"], activeforeground=COLORS["text"],
                          command=lambda k=key: self._key_pressed(k),
                          ).grid(row=0, column=col, sticky="nsew", padx=2)

    def _key_pressed(self, key: str) -> None:
        if key == "Shift":
            self._kb_shift = not self._kb_shift
        elif key in ("123", "ABC"):
            self._kb_page = "symbols" if key == "123" else "letters"
            self._kb_shift = False
        else:
            char = KEY_CHARS.get(key, key)
            if self._kb_mode == "name":
                char = char.lower()
            elif self._kb_shift and char.isalpha():
                char = char.upper()
                self._kb_shift = False
                self.root.after_idle(self._draw_keys)   # once this button's click is done
            self.type_char(char)
            return
        self.root.after_idle(self._draw_keys)

    def _show_keyboard(self, mode: str | None) -> None:
        # A keyboard takes the side panel's room, so the keys are big enough to hit:
        # the 4x4 keypad in the panel's place, a letter keyboard under the text.
        if mode == self._kb_mode:
            return
        self._kb_mode = mode
        self._kb_page, self._kb_shift = "letters", False
        for widget in (self.side, self.keypad, self.keyboard):
            widget.pack_forget()
        if mode in ("name", "text"):
            self._draw_keys()
            # Packed before the text, so the text gives way on a small screen, not the keys.
            self.keyboard.pack(fill="x", side="bottom", pady=(6, 0), before=self.lines_frame)
        else:
            panel = self.keypad if mode == "keypad" else self.side
            panel.pack(side="right", fill="y", padx=(0, 10), pady=10, before=self.status_frame)
            self._page_at = time.monotonic()    # the page shows in full again
        self.title_label.config(wraplength=self._wrap())

    def _wrap(self) -> int:
        """How wide the screen's text may be: all the width while typing on letter keys."""
        return 760 if self._kb_mode in ("name", "text") else STATUS_WRAP

    # ------------------------------------------------------------ touch buttons

    def _touchable(self, frame: tk.Frame, command) -> None:
        """Make a frame and everything in it one button: darker while held (the parts
        in the frame's colour), `command` on release over it, like a tk.Button."""
        parts, i = [frame], 0
        while i < len(parts):
            parts += parts[i].winfo_children()
            i += 1
        normal = frame.cget("bg")
        shaded = [w for w in parts if w.cget("bg") == normal]

        def up(event):
            for w in shaded:
                w.config(bg=normal)
            if self.root.winfo_containing(event.x_root, event.y_root) in parts:
                command()

        for w in parts:
            w.bind("<ButtonPress-1>", lambda _e: [p.config(bg=COLORS["panel"]) for p in shaded])
            w.bind("<ButtonRelease-1>", up)

    def _key_button(self, parent, key: str, label: str, wrap: int) -> tk.Frame:
        """A button for a "K  label" line part: the key in a badge, then the label."""
        frame = tk.Frame(parent, bg=COLORS["tab"])
        tk.Label(frame, text=key, width=2, font=("DejaVu Sans Mono", -17, "bold"),
                 fg=COLORS["prompt"], bg=COLORS["bg"]).pack(side="left", padx=(7, 8))
        tk.Label(frame, text=label, font=("DejaVu Sans", -16), anchor="w", justify="left",
                 fg=COLORS["text"], bg=COLORS["tab"], wraplength=max(60, wrap - 52),
                 ).pack(side="left", fill="x", expand=True, padx=(0, 6), pady=4)
        self._touchable(frame, lambda: self.press(key))
        return frame

    def _keypad_key(self, key: str) -> tk.Frame:
        """One key of the on-screen 4x4 keypad, with what # and * do under them."""
        bg = COLORS["success"] if key == "#" else COLORS["tab"]
        fg = COLORS["bg"] if key == "#" else (COLORS["info"] if key in "ABCD" else COLORS["text"])
        frame = tk.Frame(self.keypad, bg=bg)
        inner = tk.Frame(frame, bg=bg)
        inner.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(inner, text=key, font=("DejaVu Sans", -28, "bold"), fg=fg, bg=bg).pack()
        if key in KEYPAD_CAPTIONS:
            tk.Label(inner, text=KEYPAD_CAPTIONS[key], font=("DejaVu Sans", -12), fg=fg,
                     bg=bg).pack()
        self._touchable(frame, lambda: self.press(key))
        return frame

    def _draw_lines(self, lines: list[str]) -> None:
        """Draw a screen's lines: text, and its "K  label" lines as buttons."""
        wrap = self._wrap()
        if self._lines_drawn == (tuple(lines), wrap):
            return    # e.g. only the typed entry changed: no flicker
        self._lines_drawn = (tuple(lines), wrap)
        for child in self.lines_frame.winfo_children():
            child.destroy()
        for kind, content in blocks(lines):
            if kind == "text":
                if not content.strip():
                    tk.Frame(self.lines_frame, bg=COLORS["bg"], height=8).pack(fill="x")
                    continue
                # 18 px, as on the web page: "Hold your card on the reader to sign in or
                # out." just fits beside the side panel.
                tk.Label(self.lines_frame, text=content, font=("DejaVu Sans", -18), anchor="w",
                         justify="left", fg=COLORS["text"], bg=COLORS["bg"],
                         wraplength=wrap).pack(fill="x")
                continue
            grid = tk.Frame(self.lines_frame, bg=COLORS["bg"])
            grid.pack(fill="x", pady=1)
            columns = [[part] for part in content] if kind == "row" else menu_columns(content)
            for c, column in enumerate(columns):
                grid.grid_columnconfigure(c, weight=1, uniform="col")
                for r, (key, label) in enumerate(column):
                    grid.grid_rowconfigure(r, minsize=50)   # 46 px buttons
                    self._key_button(grid, key, label, wrap // len(columns)).grid(
                        row=r, column=c, sticky="nsew", padx=2, pady=2)

    def _build_simulator(self, reader) -> None:
        sim = tk.Frame(self.root, bg="#2a1f00")
        sim.pack(fill="x", side="bottom", before=self.footer)   # under the footer
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
        self._page, self._page_at = 0, time.monotonic()
        self._render_side()

    # ------------------------------------------------------------ updates

    def publish(self, screen: Screen) -> None:
        """Thread-safe: queue a screen to show."""
        self.events.put(("screen", screen))

    def show(self, screen: Screen, sound: bool = True) -> None:
        if sound and self.buzzer:
            self.buzzer.play(screen.buzz)
        self.title_label.config(text=screen.title, fg=COLORS.get(screen.tone, COLORS["text"]))
        self.entry_label.config(text=f"> {screen.entry}" if screen.entry is not None else "")
        self._show_keyboard(keyboard_mode(screen))
        self._draw_lines(screen.lines)
        if screen.close_app:
            self.root.after(2500, self.root.destroy)
        if getattr(screen, "restart_app", False):
            self.root.after(2500, self._restart)
        if self._revert_job:
            self.root.after_cancel(self._revert_job)
            self._revert_job = None
        if screen.hold_seconds:
            self._revert_job = self.root.after(int(screen.hold_seconds * 1000), self._revert)
        if screen.refresh_leaderboard:
            self.refresh(board=True)

    def _restart(self) -> None:
        self.restart_requested = True
        self.root.destroy()

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

    def _side_resized(self, event) -> None:
        if event.width > 1 and event.height > 1 and (event.width, event.height) != self._side_size:
            self._side_size = (event.width, event.height)
            self._render_side()

    def _side_rows(self) -> list[tuple[str, str, str]]:
        """The tab's rows as (code, name, team and time), columns padded to line up."""
        if self._tab == "here":
            now = datetime.now()
            cells = [("", r["code"], r["username"], self._team(r["section"]),
                      timefmt.format_duration(int((now - r["sign_in_at"]).total_seconds())))
                     for r in self._here]
        else:
            cells = [(str(e.rank), e.code, e.username, self._team(e.section),
                      timefmt.format_duration(e.total_seconds)) for e in self._board]
        if not cells:
            return []
        rank_w, team_w, time_w = (max(len(c[i]) for c in cells) for i in (0, 3, 4))
        return [(f"{rank:>{rank_w}} {code:<4}" if rank_w else f"{code:<4}", name,
                 f"{team:<{team_w}} {spent:>{time_w}}")
                for rank, code, name, team, spent in cells]

    def _render_side(self) -> None:
        """Draw the side panel: the page of the list showing, long names slid along."""
        here = f"Here now ({len(self._here)})"
        if self.here_tab.cget("text") != here:
            self.here_tab.config(text=here)
        rows = self._side_rows()
        if not rows:
            self._pages = 1
            self._show_side("Nobody signed in yet" if self._tab == "here" else "No one yet", "")
            return
        width, height = self._side_size
        if width <= 1 or height <= 1:   # not laid out yet: the panel's width, no pages
            width, height = SIDE_WIDTH - 20, len(rows) * self.side_font.metrics("linespace")
        line = max(1, self.side_font.metrics("linespace"))
        cols = width // max(1, self.side_font.measure("0"))
        # A list too long for the panel shows a page at a time, with "1/3" under it.
        per_page = height // line
        if len(rows) > per_page:
            per_page = max(1, (height - self.page_font.metrics("linespace")) // line)
        self._pages = -(-len(rows) // per_page)
        self._page = min(self._page, self._pages - 1)
        shown = rows[self._page * per_page:(self._page + 1) * per_page]
        name_w = max(6, cols - len(rows[0][0]) - len(rows[0][2]) - 2)
        ms = (time.monotonic() - self._page_at) * 1000
        lines = [f"{left} {slide(name, name_w, ms)} {right}" for left, name, right in shown]
        # A page stays until its long names have slid to their end and back.
        self._page_ms = max([PAGE_MS] + [marquee_ms(len(n) - name_w) for _, n, _ in shown])
        self._show_side("\n".join(lines),
                        f"{self._page + 1}/{self._pages}" if self._pages > 1 else "")

    def _show_side(self, text: str, page: str) -> None:
        # Only what changed is redrawn: this runs a few times a second.
        if self.side_label.cget("text") != text:
            self.side_label.config(text=text)
        if self.page_label.cget("text") != page:
            self.page_label.config(text=page)
            if page:
                self.page_label.place(in_=self.side_label, relx=1, rely=1, anchor="se")
            else:
                self.page_label.place_forget()

    def _team(self, section: str) -> str:
        return self.controller.users.team_name(section, short=True)[:8]

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
        available = bool(self.updates and self.updates.available)
        if available != bool(self.update_label.winfo_manager()):
            if available:
                self.update_label.pack(side="right", before=self.season_label)
            else:
                self.update_label.pack_forget()    # installed, or gone from GitHub
        self.root.after(HERE_REFRESH_MS, self._periodic_here)

    def _periodic_board(self) -> None:
        self.refresh(board=True)
        self.root.after(BOARD_REFRESH_MS, self._periodic_board)

    def _tick_side(self) -> None:
        # Turns the pages of a long list and slides long names along. Nothing moves
        # while the keyboard hides the panel.
        if self.side.winfo_ismapped():
            now = time.monotonic()
            if self._pages > 1 and (now - self._page_at) * 1000 >= self._page_ms:
                self._page, self._page_at = (self._page + 1) % self._pages, now
            self._render_side()
        self.root.after(SIDE_TICK_MS, self._tick_side)

    # ------------------------------------------------------------ input

    def press(self, key: str) -> None:
        """Queue one keypad key. Safe to call from any thread."""
        if self.buzzer:
            self.buzzer.play("key")
        self._keys.put(key)

    def type_char(self, char: str) -> None:
        """Queue one on-screen keyboard key (a character, "\b" or "\n")."""
        if self.buzzer:
            self.buzzer.play("key")
        self._keys.put(("char", char))

    def _key_loop(self) -> None:
        # One worker so keys are handled in order, off the UI thread (PIN
        # checks and database calls would otherwise freeze the screen).
        while True:
            key = self._keys.get()
            try:
                if isinstance(key, tuple):
                    screen = self.controller.handle_char(key[1])
                else:
                    screen = self.controller.handle_key(key)
            except Exception as exc:
                log.exception("key %s failed", key)
                screen = Screen("Something went wrong", [str(exc)], "error", hold_seconds=6)
            if screen:
                self.publish(screen)

    def _on_keyboard(self, event) -> None:
        if getattr(self, "_sim_entry", None) is not None and event.widget is self._sim_entry:
            return
        if self._kb_mode in ("name", "text") and event.keysym != "Escape":
            # A USB keyboard types names and passwords too.
            char = {"BackSpace": "\b", "Return": "\n", "KP_Enter": "\n"}.get(event.keysym,
                                                                             event.char)
            if char:
                self.type_char(char)
            return
        key = KEY_MAP.get(event.keysym) or event.char.upper()
        # Typing a section letter (e.g. E) presses that section's keypad key (#).
        for section in self.controller.users.sections:
            if key == section["letter"] and section["key"]:
                key = section["key"]
        if len(key) == 1 and key in "0123456789ABCD*#":
            self.press(key)


def marquee_ms(over: int) -> int:
    """How long a name `over` characters too long for its column takes to slide and rest."""
    return 2 * MARQUEE_HOLD_MS + over * MARQUEE_STEP_MS if over > 0 else 0


def slide(name: str, width: int, ms: float) -> str:
    """`name` in `width` characters, `ms` into its slide: one that fits as it is; a longer
    one shows its start, slides a character at a time to its end, rests, and starts again."""
    over = len(name) - width
    if over <= 0:
        return name.ljust(width)
    start = int((ms % marquee_ms(over) - MARQUEE_HOLD_MS) // MARQUEE_STEP_MS)
    start = min(over, max(0, start))
    return name[start:start + width]


def keyboard_mode(screen: Screen) -> str | None:
    """The on-screen keys a screen wants: None, "name", "text" or "keypad"."""
    kb = screen.keyboard
    if isinstance(kb, str):
        return kb or None
    return "name" if kb else None   # older controllers said keyboard=True for names
