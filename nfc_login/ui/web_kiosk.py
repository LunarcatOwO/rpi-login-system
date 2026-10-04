"""The kiosk screen as a local web page, for the Electron app (electron/).

Same behaviour as the Tkinter window (kiosk_window.py), but the screen is
drawn by kiosk.html in a browser window. This server listens on 127.0.0.1
only, because it accepts key presses: nothing on the network can type on the
kiosk.

    GET  /                 the kiosk page
    GET  /events           Server-Sent Events: screens, and "refresh" hints
    GET  /api/kiosk        who's here + leaderboard (+ season, team names)
    POST /key   key=7      a keypad key (the page sends computer keyboard keys)
    POST /type  char=a     the on-screen keyboard, while typing a name or a
                           Wi-Fi password: any one character, or char=back
                           (delete) / char=done
    POST /tap   uid=04AB…  simulated mode only: pretend a card was tapped
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from nfc_login.kiosk.controller import KioskController, Screen
from nfc_login.services import timefmt

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("kiosk.html")
KEYS = set("0123456789ABCD*#")
RESTART_DELAY_SECONDS = 2.5


def control_pipe():
    """The Electron app's control pipe (see _control), or None."""
    fd = os.environ.get("NFC_KIOSK_CONTROL_FD", "")
    if not fd.isdigit():
        return None
    try:
        return os.fdopen(int(fd), "w", encoding="utf-8")
    except OSError as exc:
        log.warning("kiosk: no control pipe on fd %s (%s)", fd, exc)
        return None


class WebKiosk:
    """Holds the current screen and pushes changes to every open page."""

    def __init__(self, controller: KioskController, ui_config: dict, buzzer=None,
                 simulated_reader=None, updates=None):
        self.controller = controller
        self.updates = updates
        self.cfg = ui_config
        self.buzzer = buzzer
        self.simulated_reader = simulated_reader
        self._lock = threading.Lock()
        self._listeners: list[queue.Queue] = []
        self._screen = controller.idle_screen()
        self._revert_at: float | None = None
        self._keys: queue.Queue = queue.Queue()
        self.control = control_pipe()
        self.on_restart = None          # set by __main__: stops the server so it can restart
        self.restart_requested = False
        # Background jobs (Wi-Fi, updates) show their result through this.
        controller.publish = self.publish
        threading.Thread(target=self._key_loop, daemon=True, name="keys").start()
        threading.Thread(target=self._tick, daemon=True, name="kiosk-tick").start()

    # ------------------------------------------------------------ screens

    def publish(self, screen: Screen, sound: bool = True) -> None:
        """Show a screen on every page. Safe to call from any thread."""
        if sound and self.buzzer:
            self.buzzer.play(screen.buzz)
        with self._lock:
            self._screen = screen
            self._revert_at = (time.monotonic() + screen.hold_seconds
                               if screen.hold_seconds else None)
        self._send({"type": "screen", "screen": screen_json(screen)})
        if screen.close_app:
            self._control("close")
        if getattr(screen, "restart_app", False):
            self._control("restart")
        if screen.refresh_leaderboard:
            self._send({"type": "refresh"})

    def _control(self, command: str) -> None:
        """Ask the Electron app to close or restart (System menu, or an installed update).

        The app passes a private pipe as NFC_KIOSK_CONTROL_FD and only listens
        there, so nothing written to the log (like a web page request) can
        close the kiosk. Started without that pipe (an older copy of the app
        that is still running, or a plain --web-ui run), a restart restarts
        just this Python program, which loads the new code all the same.
        """
        if self.control is not None:
            try:
                self.control.write(command + "\n")
                self.control.flush()
                return
            except OSError as exc:
                log.warning("kiosk: couldn't reach the app (%s)", exc)
        if command == "close":
            # Older copies of the app quit on this line.
            log.info("kiosk: close requested from the admin menu")
        elif self.on_restart is not None:
            log.info("kiosk: restarting the kiosk program")
            self.restart_requested = True
            # A moment to read the screen first.
            threading.Timer(RESTART_DELAY_SECONDS, self.on_restart).start()

    def current(self) -> dict:
        with self._lock:
            return screen_json(self._screen)

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

    def _tick(self) -> None:
        # Once a second: drop half-typed input, and go back to the idle screen
        # once a result has been shown long enough.
        while True:
            time.sleep(1)
            try:
                screen = self.controller.check_timeout()
                if screen:
                    self.publish(screen)
                    continue
                with self._lock:
                    due = self._revert_at is not None and time.monotonic() >= self._revert_at
                if due and self.controller.state == "idle":
                    self.publish(self.controller.idle_screen(), sound=False)
            except Exception:
                log.exception("kiosk tick failed")

    # ------------------------------------------------------------ listeners

    def listen(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._listeners.append(q)
        return q

    def unlisten(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def _send(self, event: dict) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for q in listeners:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # a stuck page; it reloads its state when it reconnects

    # ------------------------------------------------------------ side panel data

    def side_data(self) -> dict:
        att = self.controller.attendance
        users = self.controller.users
        now = datetime.now()
        here = [{
            "code": r["code"], "name": r["username"],
            "team": users.team_name(r["section"], short=True),
            "time": timefmt.format_duration(int((now - r["sign_in_at"]).total_seconds())),
        } for r in att.currently_signed_in()]
        board = [{
            "rank": e.rank, "code": e.code, "name": e.username,
            "team": users.team_name(e.section, short=True),
            "time": timefmt.format_duration(e.total_seconds),
        } for e in att.leaderboard(self.cfg["leaderboard_size"])]
        keys = [{"key": s["key"], "name": s["name"]} for s in users.sections if s["key"]]
        return {"season": att.active_season_name(), "here": here, "board": board,
                "keys": keys, "mentors": self.controller.mentors,
                "simulated": self.simulated_reader is not None,
                "update": bool(self.updates and self.updates.available)}


def screen_json(screen: Screen) -> dict:
    return {"title": screen.title, "lines": screen.lines, "tone": screen.tone,
            "entry": screen.entry, "keyboard": keyboard_mode(screen),
            "busy": getattr(screen, "busy", False), "hold": screen.hold_seconds}


def keyboard_mode(screen: Screen) -> str | None:
    """The on-screen keys a screen wants: None, "name" or "text" (letter keyboards)."""
    kb = screen.keyboard
    if isinstance(kb, str):
        return kb or None
    return "name" if kb else None   # older controllers said keyboard=True for names


def _typed(char: str) -> str | None:
    """POST /type's char -> what handle_char takes, or None if it isn't one key."""
    if char in ("back", "done"):
        return "\b" if char == "back" else "\n"
    return char if len(char) == 1 and char.isprintable() else None


def make_handler(kiosk: WebKiosk):
    class Handler(BaseHTTPRequestHandler):
        server_version = "nfc-kiosk"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, body: bytes, content_type: str, status=HTTPStatus.OK):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status=HTTPStatus.OK):
            self._send(json.dumps(data).encode(), "application/json", status)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/kiosk":
                self._json(kiosk.side_data())
            elif path == "/events":
                self._events()
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            # Only the page itself may post: other web pages (Origin) and
            # DNS-rebinding tricks (Host) are turned away.
            port = self.server.server_address[1]
            if (self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}")
                    or self.headers.get("Origin") not in (None, self._origin())):
                self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
                return
            length = min(int(self.headers.get("Content-Length") or 0), 1000)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            path = urlparse(self.path).path
            if path == "/key" and form.get("key", "").upper() in KEYS:
                kiosk.press(form["key"].upper())
                self._json({"ok": True})
            elif path == "/type" and _typed(form.get("char", "")):
                kiosk.type_char(_typed(form["char"]))
                self._json({"ok": True})
            elif path == "/tap" and kiosk.simulated_reader is not None and form.get("uid"):
                kiosk.simulated_reader.tap(form["uid"])
                self._json({"ok": True})
            else:
                self._json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)

        def _origin(self) -> str:
            host, port = self.server.server_address[:2]
            return f"http://{host}:{port}"

        def _events(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            q = kiosk.listen()
            try:
                self._event({"type": "screen", "screen": kiosk.current()})
                while True:
                    try:
                        self._event(q.get(timeout=15))
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                kiosk.unlisten(q)

        def _event(self, data: dict):
            self.wfile.write(b"data: " + json.dumps(data).encode() + b"\n\n")
            self.wfile.flush()

    return Handler


def serve(kiosk: WebKiosk, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(kiosk))
    server.daemon_threads = True
    log.info("kiosk screen at http://127.0.0.1:%d/", port)
    return server
