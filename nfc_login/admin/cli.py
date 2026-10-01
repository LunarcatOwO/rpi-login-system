"""Admin command-line tool.

    python3 -m nfc_login.admin --help

Run it on the Pi (over SSH is fine). Commands that use the NFC reader
(`tag enroll` without --uid, `tag read`) need the kiosk service stopped first,
since only one program can talk to the reader at a time.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from nfc_login.app import build_services
from nfc_login.config import load_config
from nfc_login.services import timefmt
from nfc_login.services.attendance import AttendanceError
from nfc_login.services.seasons import SeasonError
from nfc_login.services.users import UserError


def _ask_pin(prompt: str = "PIN (4-8 digits): ") -> str:
    pin = getpass.getpass(prompt)
    if getpass.getpass("Again: ") != pin:
        raise UserError("PINs did not match.")
    return pin


def _table(rows: list[list], headers: list[str]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)] if rows else \
        [len(h) for h in headers]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(x).ljust(w) for x, w in zip(row, widths)))


def _wait_for_card(config) -> tuple[object, str]:
    from nfc_login.hardware import create_reader
    if config.hardware["mode"] == "simulated":
        raise UserError("No reader in simulated mode; pass --uid instead.")
    reader = create_reader(config)
    print("Hold the card on the reader... (Ctrl+C to cancel)")
    while True:
        uid = reader.read_uid()
        if uid:
            return reader, uid


# ---------------------------------------------------------------- commands


def cmd_init_db(s, args, config):
    if args.create_database:
        s.db.create_database()
    s.db.apply_schema()
    season = s.seasons.ensure_active()
    print(f"Schema ready. Active season: {season['name']}")
    if not s.users.admin_pin_set():
        print("Next: set the kiosk admin PIN with `python3 -m nfc_login.admin set-admin-pin`")


def cmd_set_admin_pin(s, args, config):
    s.users.set_admin_pin(_ask_pin("New admin PIN (4-8 digits): "))
    print("Admin PIN saved.")


def cmd_user_add(s, args, config):
    pin = _ask_pin() if args.pin else None
    user_id = s.users.add(args.username, pin)
    print(f"Created user {args.username!r} with ID {user_id}.")
    print(f"Enroll a card: tap A on the kiosk keypad, or `tag enroll {user_id}`")


def cmd_user_list(s, args, config):
    rows = [[u["id"], u["username"], "yes" if u["pin_hash"] else "no",
             "active" if u["is_active"] else "inactive"]
            for u in s.users.list(include_inactive=args.all)]
    _table(rows, ["ID", "Username", "PIN", "Status"])


def cmd_user_show(s, args, config):
    st = s.attendance.user_stats(args.user_id)
    rank = f"#{st.rank} of {st.ranked_users}" if st.rank else "-"
    print(f"ID:            {st.user_id}")
    print(f"Username:      {st.username}")
    print(f"Season:        {st.season_name}")
    print(f"Time:          {st.total_text}")
    print(f"Rank:          {rank}")
    print(f"Signed in now: {'yes' if st.signed_in else 'no'}")
    print(f"Last sign in:  {timefmt.format_timestamp(st.last_sign_in)}")
    print(f"Last sign out: {timefmt.format_timestamp(st.last_sign_out)}")


def cmd_user_rename(s, args, config):
    s.users.rename(args.user_id, args.username)
    print("Renamed.")


def cmd_user_set_pin(s, args, config):
    s.users.set_pin(args.user_id, None if args.clear else _ask_pin())
    print("PIN cleared." if args.clear else "PIN saved.")


def cmd_user_deactivate(s, args, config):
    s.users.set_active(args.user_id, False)
    print("User deactivated (history kept, hidden from leaderboard, cards stop working).")


def cmd_user_activate(s, args, config):
    s.users.set_active(args.user_id, True)
    print("User reactivated.")


def cmd_tag_enroll(s, args, config):
    reader = None
    uid = args.uid
    if not uid:
        reader, uid = _wait_for_card(config)
    user = s.users.enroll_tag(uid, args.user_id)
    print(f"Card {uid.upper()} enrolled for {user['username']} (ID {user['id']}).")
    if reader and config.hardware["nfc"]["write_tags"]:
        from nfc_login.tags.payload import build_message
        try:
            capacity = reader.ndef_capacity()
            if capacity is None:
                print("This card type can't store info (not an NTAG); it still works for sign-in.")
            else:
                stats = s.attendance.user_stats(args.user_id)
                reader.write_ndef(build_message(stats, config.tag["site_url"], capacity))
                print("Card info written.")
        except Exception as exc:  # noqa: BLE001 - report and carry on
            print(f"Card info not written: {exc}")


def cmd_tag_list(s, args, config):
    rows = [[t["uid"], t["user_id"], t["username"], t["enrolled_at"],
             "active" if t["is_active"] else "removed"] for t in s.users.list_tags()]
    _table(rows, ["UID", "User ID", "Username", "Enrolled", "Status"])


def cmd_tag_remove(s, args, config):
    s.users.remove_tag(args.uid)
    print("Card removed. It will no longer sign anyone in.")


def cmd_tag_read(s, args, config):
    _reader, uid = _wait_for_card(config)
    print(f"UID: {uid}")


def cmd_season_show(s, args, config):
    season = s.seasons.active()
    print(f"Active season: {season['name']} (since {season['started_at']})" if season
          else "No active season.")


def cmd_season_list(s, args, config):
    rows = [[x["id"], x["name"], x["started_at"], x["ended_at"] or "-",
             "active" if x["is_active"] else "archived"] for x in s.seasons.list()]
    _table(rows, ["ID", "Name", "Started", "Ended", "Status"])


def cmd_season_new(s, args, config):
    current = s.seasons.active()
    name = args.name or str(timefmt.now().year)
    if not args.yes:
        print(f"This ends season {current['name'] if current else '(none)'!r}, signs everyone "
              f"out, and starts season {name!r} with everyone at 0h 00m.")
        print("Users, cards and all old data are kept.")
        if input("Type YES to continue: ").strip() != "YES":
            print("Cancelled.")
            return
    summary = s.seasons.start_new(name)
    print(f"Started season {summary.new_season}.")
    if summary.old_season:
        print(f"Ended season {summary.old_season}; signed out {summary.signed_out} user(s).")
        for path in summary.archive_files:
            print(f"Archived: {path}")


def cmd_season_leaderboard(s, args, config):
    season, entries = s.seasons.leaderboard(args.name)
    print(f"Season {season['name']}")
    _table([[e.rank, e.user_id, e.username, timefmt.format_duration(e.total_seconds)]
            for e in entries], ["Rank", "ID", "Username", "Time"])


def cmd_season_export(s, args, config):
    name = args.name or s.seasons.active()["name"]
    for path in s.seasons.export(name):
        print(f"Wrote {path}")


def cmd_sessions_open(s, args, config):
    rows = [[r["user_id"], r["username"], r["sign_in_at"], r["sign_in_method"]]
            for r in s.attendance.currently_signed_in()]
    _table(rows, ["User ID", "Username", "Signed in", "Method"])


def cmd_sessions_sign_out_all(s, args, config):
    print(f"Signed out {s.attendance.sign_out_everyone()} user(s).")


def cmd_sessions_close_stale(s, args, config):
    print(f"Closed {s.attendance.close_stale_sessions()} stale session(s).")


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m nfc_login.admin",
                                description="Admin tool for the NFC login system")
    p.add_argument("--config", help="path to config.toml")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("init-db", help="create tables and the first season")
    c.add_argument("--create-database", action="store_true",
                   help="also CREATE DATABASE (needs a privileged DB user)")
    c.set_defaults(func=cmd_init_db)

    sub.add_parser("set-admin-pin", help="set the PIN for the kiosk admin menu"
                   ).set_defaults(func=cmd_set_admin_pin)

    user = sub.add_parser("user", help="manage users").add_subparsers(dest="action", required=True)
    c = user.add_parser("add", help="create a user")
    c.add_argument("username")
    c.add_argument("--pin", action="store_true", help="also set a keypad PIN")
    c.set_defaults(func=cmd_user_add)
    c = user.add_parser("list", help="list users")
    c.add_argument("--all", action="store_true", help="include deactivated users")
    c.set_defaults(func=cmd_user_list)
    for name, func, helptext in [("show", cmd_user_show, "time, rank and last sign in/out"),
                                 ("deactivate", cmd_user_deactivate, "hide a user"),
                                 ("activate", cmd_user_activate, "un-hide a user")]:
        c = user.add_parser(name, help=helptext)
        c.add_argument("user_id", type=int)
        c.set_defaults(func=func)
    c = user.add_parser("rename", help="change a username")
    c.add_argument("user_id", type=int)
    c.add_argument("username")
    c.set_defaults(func=cmd_user_rename)
    c = user.add_parser("set-pin", help="set or clear a user's keypad PIN")
    c.add_argument("user_id", type=int)
    c.add_argument("--clear", action="store_true")
    c.set_defaults(func=cmd_user_set_pin)

    tag = sub.add_parser("tag", help="manage NFC cards").add_subparsers(dest="action",
                                                                         required=True)
    c = tag.add_parser("enroll", help="link a card to a user")
    c.add_argument("user_id", type=int)
    c.add_argument("--uid", help="card UID in hex (skip to read it from the reader)")
    c.set_defaults(func=cmd_tag_enroll)
    tag.add_parser("list", help="list cards").set_defaults(func=cmd_tag_list)
    c = tag.add_parser("remove", help="stop a card from working")
    c.add_argument("uid")
    c.set_defaults(func=cmd_tag_remove)
    tag.add_parser("read", help="print the UID of a card on the reader").set_defaults(
        func=cmd_tag_read)

    season = sub.add_parser("season", help="seasons and resets").add_subparsers(
        dest="action", required=True)
    season.add_parser("show", help="show the active season").set_defaults(func=cmd_season_show)
    season.add_parser("list", help="list all seasons").set_defaults(func=cmd_season_list)
    c = season.add_parser("new", help="season reset: archive this season and start a new one")
    c.add_argument("name", nargs="?", help="new season name (default: current year)")
    c.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    c.set_defaults(func=cmd_season_new)
    c = season.add_parser("leaderboard", help="show a season's leaderboard")
    c.add_argument("name", nargs="?", help="season name (default: active)")
    c.set_defaults(func=cmd_season_leaderboard)
    c = season.add_parser("export", help="write a season's CSV files to the archive folder")
    c.add_argument("name", nargs="?", help="season name (default: active)")
    c.set_defaults(func=cmd_season_export)

    sessions = sub.add_parser("sessions", help="who is signed in").add_subparsers(
        dest="action", required=True)
    sessions.add_parser("open", help="list people signed in now").set_defaults(
        func=cmd_sessions_open)
    sessions.add_parser("sign-out-all", help="sign everyone out, crediting their time"
                        ).set_defaults(func=cmd_sessions_sign_out_all)
    sessions.add_parser("close-stale", help="close sessions past max_session_hours (no credit)"
                        ).set_defaults(func=cmd_sessions_close_stale)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    services = build_services(config)
    try:
        args.func(services, args, config)
    except (UserError, SeasonError, AttendanceError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    return 0
