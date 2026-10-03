"""Small web server on the Pi, for phones and laptops on the same network.

    http://<pi-address>:8080/        live "who's here" + leaderboard (anyone)
    http://<pi-address>:8080/admin   add/subtract hours, add users (admin PIN)
    http://<pi-address>:8080/api/status   the live data as JSON

Card enrollment is deliberately not offered here: it needs the physical card
on the reader, so it's done at the kiosk (admin menu) or with the admin CLI.

Standard library only (http.server). It runs inside the kiosk process, or on
its own with ``python3 -m nfc_login.web``.
"""

from __future__ import annotations

import html
import json
import logging
import secrets
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from nfc_login.app import Services
from nfc_login.services import ids, timefmt
from nfc_login.services.attendance import AttendanceError
from nfc_login.services.users import UserError

log = logging.getLogger(__name__)

SESSION_COOKIE = "nfc_admin"
SESSION_SECONDS = 30 * 60
MAX_LOGIN_FAILURES = 5
LOCKOUT_SECONDS = 60


def status_payload(services: Services, sections: list[dict], leaderboard_size: int = 50) -> dict:
    """Everything the live page shows, computed fresh from the database."""
    att = services.attendance
    now = datetime.now()
    here = [{
        "code": r["code"],
        "name": r["username"],
        "section": r["section"],
        "team": services.users.team_name(r["section"]),
        "since": r["sign_in_at"].isoformat(),
        "seconds": max(0, int((now - r["sign_in_at"]).total_seconds())),
    } for r in att.currently_signed_in()]
    board = [{
        "rank": e.rank,
        "code": e.code,
        "name": e.username,
        "section": e.section,
        "team": services.users.team_name(e.section),
        "total_seconds": e.total_seconds,
        "total": timefmt.format_duration(e.total_seconds),
    } for e in att.leaderboard(leaderboard_size)]
    return {
        "season": att.active_season_name(),
        "now": now.isoformat(timespec="seconds"),
        "sections": [{"letter": s["letter"], "name": s["name"]} for s in sections]
        + ([{"letter": ids.UNSORTED, "name": ids.UNSORTED_NAME}]
           if any(p["section"] == ids.UNSORTED for p in here) else []),
        "here": here,
        "leaderboard": board,
    }


class AdminSessions:
    """In-memory admin logins plus a PIN lockout.

    Each login remembers the admin PIN (its hash) it was made with, so
    changing the PIN, on the kiosk or the command line, logs everyone out.
    """

    def __init__(self):
        self._tokens: dict[str, tuple[float, str | None]] = {}   # token -> (expiry, PIN hash)
        self._failures = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def create(self, pin_hash: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = (time.monotonic() + SESSION_SECONDS, pin_hash)
            self._failures = 0
        return token

    def valid(self, token: str | None, pin_hash: str | None = None) -> bool:
        with self._lock:
            expiry, made_with = self._tokens.get(token or "", (0.0, None))
            if expiry < time.monotonic() or made_with != pin_hash:
                self._tokens.pop(token or "", None)
                return False
            self._tokens[token] = (time.monotonic() + SESSION_SECONDS, made_with)  # sliding
            return True

    def end(self, token: str | None) -> None:
        with self._lock:
            self._tokens.pop(token or "", None)

    def locked(self) -> bool:
        return self._locked_until > time.monotonic()

    def failed(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= MAX_LOGIN_FAILURES:
                self._failures = 0
                self._locked_until = time.monotonic() + LOCKOUT_SECONDS


def make_handler(services: Services, sections: list[dict], refresh_seconds: float):
    sessions = AdminSessions()
    section_names = {s["letter"]: s["name"] for s in sections}

    class Handler(BaseHTTPRequestHandler):
        server_version = "nfc-login"

        def log_message(self, fmt, *args):  # route through logging, not stderr
            # Anyone on the network picks the request text: show control characters
            # escaped so it can't fake extra log lines.
            message = (fmt % args).encode("unicode_escape").decode("ascii")
            log.info("%s %s", self.address_string(), message)

        # ---------------------------------------------------------- helpers

        def _send(self, body: bytes, content_type: str, status=HTTPStatus.OK, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _html(self, text: str, status=HTTPStatus.OK, headers=None):
            self._send(text.encode(), "text/html; charset=utf-8", status, headers)

        def _redirect(self, location: str, headers=None):
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _token(self) -> str | None:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return cookie[SESSION_COOKIE].value if SESSION_COOKIE in cookie else None

        def _is_admin(self) -> bool:
            token = self._token()
            return bool(token) and sessions.valid(token, services.users.admin_pin_hash())

        def _form(self) -> dict[str, str]:
            length = min(int(self.headers.get("Content-Length") or 0), 10_000)
            data = parse_qs(self.rfile.read(length).decode())
            return {k: v[0].strip() for k, v in data.items()}

        def _same_origin(self) -> bool:
            # Belt and braces with the SameSite cookie: refuse cross-site form posts.
            origin = self.headers.get("Origin")
            return origin is None or urlparse(origin).netloc == self.headers.get("Host")

        # ---------------------------------------------------------- routes

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                page = resources.files("nfc_login.web").joinpath("live.html").read_text()
                self._html(page.replace("{{REFRESH_MS}}", str(int(refresh_seconds * 1000))))
            elif path == "/api/status":
                body = json.dumps(status_payload(services, sections)).encode()
                self._send(body, "application/json")
            elif path == "/admin":
                if self._is_admin():
                    self._html(self._admin_page(*self._flash()))
                else:
                    self._html(login_page())
            else:
                self._html("<h1>Not found</h1>", HTTPStatus.NOT_FOUND)

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._same_origin():
                self._html("<h1>Forbidden</h1>", HTTPStatus.FORBIDDEN)
                return
            form = self._form()
            if path == "/admin/login":
                self._login(form)
                return
            if not self._is_admin():
                self._redirect("/admin")
                return
            actions = {
                "/admin/logout": self._logout,
                "/admin/adjust": self._adjust,
                "/admin/users": self._add_user,
                "/admin/team": self._change_team,
                "/admin/signout-all": self._sign_out_all,
            }
            action = actions.get(path)
            if action:
                action(form)
            else:
                self._html("<h1>Not found</h1>", HTTPStatus.NOT_FOUND)

        # ---------------------------------------------------------- admin actions

        def _login(self, form):
            if sessions.locked():
                self._html(login_page("Too many wrong PINs. Wait a minute."),
                           HTTPStatus.TOO_MANY_REQUESTS)
                return
            if services.users.check_admin_pin(form.get("pin", "")):
                token = sessions.create(services.users.admin_pin_hash())
                cookie = (f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; "
                          f"Max-Age={SESSION_SECONDS}")
                self._redirect("/admin", {"Set-Cookie": cookie})
            else:
                sessions.failed()
                self._html(login_page("Wrong PIN."), HTTPStatus.UNAUTHORIZED)

        def _logout(self, form):
            sessions.end(self._token())
            self._redirect("/admin", {"Set-Cookie": f"{SESSION_COOKIE}=; Max-Age=0; Path=/"})

        def _adjust(self, form):
            try:
                user = services.users.get_by_code(form.get("code", ""))
                hours = int(form.get("hours") or 0)
                minutes = int(form.get("minutes") or 0)
                if hours < 0 or not 0 <= minutes < 60:
                    raise ValueError("Hours must be 0 or more and minutes 0-59.")
                seconds = (hours * 60 + minutes) * 60
                if form.get("direction") == "subtract":
                    seconds = -seconds
                stats = services.attendance.adjust(user["id"], seconds,
                                                   form.get("reason", ""), via="web")
            except (UserError, AttendanceError, ValueError) as exc:
                self._redirect_flash(str(exc), error=True)
                return
            verb = "Added" if seconds > 0 else "Subtracted"
            self._redirect_flash(f"{verb} {timefmt.format_duration(abs(seconds))} "
                                 f"{'to' if seconds > 0 else 'from'} {stats.username} "
                                 f"({stats.code}). New total: {stats.total_text}.")

        def _add_user(self, form):
            try:
                user = services.users.add(form.get("username", ""), form.get("section", ""),
                                          form.get("pin") or None)
            except (UserError, ValueError) as exc:
                self._redirect_flash(str(exc), error=True)
                return
            self._redirect_flash(f"Created {user['username']} with ID {user['code']}. "
                                 "Enroll their card at the kiosk: * → admin PIN → 1.")

        def _change_team(self, form):
            try:
                user = services.users.get_by_code(form.get("code", ""))
                user = services.users.move(user["id"], form.get("section", ""))
            except UserError as exc:
                self._redirect_flash(str(exc), error=True)
                return
            self._redirect_flash(f"{user['username']} is now in "
                                 f"{services.users.team_name(user['section'])} "
                                 f"with ID {user['code']}.")

        def _sign_out_all(self, form):
            count = services.attendance.sign_out_everyone()
            self._redirect_flash(f"Signed out {count} people.")

        # The message after an action travels in the redirect's query string.
        def _redirect_flash(self, message: str, error: bool = False):
            from urllib.parse import urlencode
            self._redirect("/admin?" + urlencode({"msg": message, "err": int(error)}))

        def _flash(self) -> tuple[str, bool]:
            query = parse_qs(urlparse(self.path).query)
            return query.get("msg", [""])[0], query.get("err", ["0"])[0] == "1"

        def _admin_page(self, message: str, error: bool) -> str:
            return admin_page(services, section_names, message, error)

    return Handler


# ------------------------------------------------------------------ pages

STYLE = """
:root { color-scheme: light dark; --bg:#f5f7fa; --card:#fff; --text:#17202a; --muted:#5b6876;
        --line:#dde3ea; --accent:#2f7de1; --ok:#1f9d55; --err:#d64545; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#101418; --card:#1b2129; --text:#e8edf2; --muted:#8a97a6; --line:#2c3440; } }
* { box-sizing: border-box; }
body { margin:0; font-family: system-ui, sans-serif; background:var(--bg); color:var(--text); }
main { max-width: 960px; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.4rem; margin: 8px 0 16px; } h2 { font-size: 1.1rem; margin: 0 0 12px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px;
        margin-bottom:16px; }
table { width:100%; border-collapse:collapse; } td, th { text-align:left; padding:6px 4px;
        border-bottom:1px solid var(--line); } th { color:var(--muted); font-weight:600; }
label { display:block; margin:8px 0 4px; color:var(--muted); font-size:.9rem; }
input, select, button { font:inherit; padding:8px 10px; border-radius:6px;
        border:1px solid var(--line); background:var(--bg); color:var(--text); }
button { background:var(--accent); color:#fff; border:0; cursor:pointer; }
.row { display:flex; gap:12px; flex-wrap:wrap; align-items:end; }
.msg { padding:10px 12px; border-radius:6px; margin-bottom:16px; background:var(--ok); color:#fff; }
.msg.err { background:var(--err); }
.muted { color:var(--muted); }
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
            f"<body><main>{body}</main></body></html>")


def login_page(message: str = "") -> str:
    msg = f"<div class='msg err'>{html.escape(message)}</div>" if message else ""
    return _page("Admin login", f"""
<h1>Admin</h1>{msg}
<form class="card" method="post" action="/admin/login">
  <label for="pin">Admin PIN</label>
  <div class="row"><input id="pin" name="pin" type="password" inputmode="numeric"
       autocomplete="current-password" autofocus required>
  <button>Log in</button></div>
</form>
<p><a href="/">← Who's here</a></p>""")


def admin_page(services: Services, section_names: dict[str, str], message: str,
               error: bool) -> str:
    e = html.escape
    users = services.users.list()
    totals = {entry.code: entry for entry in services.attendance.leaderboard()}
    adjustments = services.attendance.recent_adjustments(20)
    season = services.attendance.active_season_name()

    user_options = "".join(f"<option value='{e(u['code'])}'>{e(u['code'])} — {e(u['username'])}"
                           f"</option>" for u in users)
    section_options = "".join(
        f"<option value='{e(k)}'>{e(v)} ({e(ids.format_code(k, 1))}, "
        f"{e(ids.format_code(k, 2))}...)</option>" for k, v in section_names.items())
    user_rows = "".join(
        f"<tr><td>{e(u['code'])}</td><td>{e(u['username'])}</td>"
        f"<td>{e(section_names.get(u['section'], ids.UNSORTED_NAME))}</td>"
        f"<td>{e(timefmt.format_duration(totals[u['code']].total_seconds)) if u['code'] in totals else '-'}</td>"
        f"<td>{'#' + str(totals[u['code']].rank) if u['code'] in totals else '-'}</td></tr>"
        for u in users)
    adj_rows = "".join(
        f"<tr><td>{a['created_at']:%Y-%m-%d %H:%M}</td><td>{e(a['code'])} {e(a['username'])}</td>"
        f"<td>{'+' if a['seconds'] > 0 else '−'}{e(timefmt.format_duration(abs(a['seconds'])))}</td>"
        f"<td>{e(a['reason'])}</td><td class='muted'>{e(a['created_via'])}</td></tr>"
        for a in adjustments)
    msg = (f"<div class='msg{' err' if error else ''}'>{e(message)}</div>" if message else "")

    return _page("Admin", f"""
<div class="row" style="justify-content:space-between">
  <h1>Admin · Season {e(season)}</h1>
  <form method="post" action="/admin/logout"><button>Log out</button></form>
</div>
{msg}
<section class="card">
  <h2>Add or subtract hours</h2>
  <form method="post" action="/admin/adjust" class="row">
    <div><label>User</label><select name="code" required>{user_options}</select></div>
    <div><label>Add / subtract</label><select name="direction">
      <option value="add">Add</option><option value="subtract">Subtract</option></select></div>
    <div><label>Hours</label><input name="hours" type="number" min="0" max="999" value="0"
         style="width:6em"></div>
    <div><label>Minutes</label><input name="minutes" type="number" min="0" max="59" value="0"
         style="width:6em"></div>
    <div style="flex:1;min-width:180px"><label>Reason</label>
         <input name="reason" maxlength="255" placeholder="e.g. forgot to sign out 9/14"
         style="width:100%"></div>
    <div><button>Save</button></div>
  </form>
</section>
<section class="card">
  <h2>Add a user</h2>
  <form method="post" action="/admin/users" class="row">
    <div style="flex:1;min-width:180px"><label>Name</label>
         <input name="username" required maxlength="64" style="width:100%"></div>
    <div><label>Team</label><select name="section">{section_options}</select></div>
    <div><label>Keypad PIN (optional)</label><input name="pin" inputmode="numeric"
         pattern="[0-9]{{4,8}}" style="width:8em"></div>
    <div><button>Create</button></div>
  </form>
  <p class="muted">The new user gets the next free ID in their team (e.g. B004, or 004
  for a mentor).
  Cards are enrolled at the kiosk: press <b>*</b>, enter the admin PIN, then <b>1</b>.</p>
</section>
<section class="card">
  <h2>Change someone's team</h2>
  <form method="post" action="/admin/team" class="row">
    <div><label>User</label><select name="code" required>{user_options}</select></div>
    <div><label>New team</label><select name="section">{section_options}</select></div>
    <div><button>Move</button></div>
  </form>
  <p class="muted">They get the next free ID in the new team, and their old ID stops
  working. People imported from the old system start with a U ID until they're moved
  (or an admin picks their team at the kiosk when they first tap their card).</p>
</section>
<section class="card">
  <h2>Recent adjustments</h2>
  <table><tr><th>When</th><th>User</th><th>Change</th><th>Reason</th><th>From</th></tr>
  {adj_rows or "<tr><td colspan=5 class='muted'>None yet</td></tr>"}</table>
</section>
<section class="card">
  <h2>Users</h2>
  <table><tr><th>ID</th><th>Name</th><th>Team</th><th>Season time</th><th>Rank</th></tr>
  {user_rows or "<tr><td colspan=5 class='muted'>No users yet</td></tr>"}</table>
</section>
<section class="card">
  <h2>End of day</h2>
  <form method="post" action="/admin/signout-all"
        onsubmit="return confirm('Sign everyone out now?')">
    <button>Sign everyone out</button></form>
</section>
<p><a href="/">← Who's here</a></p>""")


# ------------------------------------------------------------------ running


def create_server(services: Services, sections: list[dict], host: str, port: int,
                  refresh_seconds: float = 3) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(services, sections, refresh_seconds))
    server.daemon_threads = True
    return server


def start_in_background(services: Services, config) -> ThreadingHTTPServer | None:
    web = config.web
    if not web["enabled"]:
        return None
    try:
        server = create_server(services, config.sections, web["host"], web["port"],
                               web["refresh_seconds"])
    except OSError as exc:
        log.error("web page not started on port %s: %s", web["port"], exc)
        return None
    threading.Thread(target=server.serve_forever, daemon=True, name="web").start()
    log.info("live page on http://%s:%s/", web["host"], web["port"])
    return server
