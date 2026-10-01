"""Admin command-line tool.

    python3 -m nfc_login.admin --help

Run it on the Pi (over SSH is fine). Users are referred to by their ID,
a section letter plus number such as A07. Commands that use the NFC reader
(`tag enroll` without --uid, `tag read`) need the kiosk service stopped first,
since only one program can talk to the reader at a time.
"""

from __future__ import annotations

import argparse
import getpass
import sys

import pymysql

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
    user = s.users.add(args.username, args.section, pin)
    print(f"Created user {user['username']!r} with ID {user['code']}.")
    print(f"Enroll a card at the kiosk (* → admin PIN → 1), or `tag enroll {user['code']}`")


def cmd_user_list(s, args, config):
    names = s.users.section_names
    rows = [[u["code"], u["username"], names.get(u["section"], u["section"]),
             "yes" if u["pin_hash"] else "no", "active" if u["is_active"] else "inactive"]
            for u in s.users.list(include_inactive=args.all)]
    _table(rows, ["ID", "Username", "Section", "PIN", "Status"])


def cmd_user_show(s, args, config):
    st = s.attendance.user_stats(s.users.get_by_code(args.code)["id"])
    rank = f"#{st.rank} of {st.ranked_users}" if st.rank else "-"
    print(f"ID:            {st.code}")
    print(f"Username:      {st.username}")
    print(f"Season:        {st.season_name}")
    print(f"Time:          {st.total_text}")
    print(f"Rank:          {rank}")
    print(f"Signed in now: {'yes' if st.signed_in else 'no'}")
    print(f"Last sign in:  {timefmt.format_timestamp(st.last_sign_in)}")
    print(f"Last sign out: {timefmt.format_timestamp(st.last_sign_out)}")


def cmd_user_rename(s, args, config):
    s.users.rename(args.code, args.username)
    print("Renamed.")


def cmd_user_set_pin(s, args, config):
    s.users.set_pin(args.code, None if args.clear else _ask_pin())
    print("PIN cleared." if args.clear else "PIN saved.")


def cmd_user_deactivate(s, args, config):
    s.users.set_active(args.code, False)
    print("User deactivated (history kept, hidden from leaderboard, cards stop working).")


def cmd_user_activate(s, args, config):
    s.users.set_active(args.code, True)
    print("User reactivated.")


def cmd_tag_enroll(s, args, config):
    target = s.users.get_by_code(args.code)
    reader = None
    uid = args.uid
    if not uid:
        reader, uid = _wait_for_card(config)
    user = s.users.enroll_tag(uid, target["id"])
    print(f"Card {uid.upper()} enrolled for {user['username']} ({user['code']}).")
    if reader and config.hardware["nfc"]["write_tags"]:
        from nfc_login.tags.payload import build_message
        try:
            capacity = reader.ndef_capacity()
            if capacity is None:
                print("This card type can't store info (not an NTAG); it still works for sign-in.")
            else:
                stats = s.attendance.user_stats(target["id"])
                reader.write_ndef(build_message(stats, config.tag["site_url"], capacity))
                print("Card info written.")
        except Exception as exc:  # noqa: BLE001 - report and carry on
            print(f"Card info not written: {exc}")


def cmd_tag_list(s, args, config):
    rows = [[t["uid"], t["code"], t["username"], t["enrolled_at"],
             "active" if t["is_active"] else "removed"] for t in s.users.list_tags()]
    _table(rows, ["UID", "ID", "Username", "Enrolled", "Status"])


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
    _table([[e.rank, e.code, e.username, timefmt.format_duration(e.total_seconds)]
            for e in entries], ["Rank", "ID", "Username", "Time"])


def cmd_season_export(s, args, config):
    name = args.name or s.seasons.active()["name"]
    for path in s.seasons.export(name):
        print(f"Wrote {path}")


def cmd_sessions_open(s, args, config):
    rows = [[r["code"], r["username"], r["sign_in_at"], r["sign_in_method"]]
            for r in s.attendance.currently_signed_in()]
    _table(rows, ["ID", "Username", "Signed in", "Method"])


def cmd_sessions_sign_out_all(s, args, config):
    print(f"Signed out {s.attendance.sign_out_everyone()} user(s).")


def _parse_duration(text: str) -> int:
    """'1h30m', '2h', '45m', '1:30' -> seconds."""
    t = text.strip().lower().replace(" ", "")
    if ":" in t:
        hours, minutes = t.split(":", 1)
    else:
        hours, _, rest = t.partition("h") if "h" in t else ("0", "", t)
        minutes = rest.rstrip("m") or "0"
    if not (hours or "0").isdigit() or not minutes.isdigit() or int(minutes) >= 60:
        raise ValueError(f"Can't read {text!r}. Use e.g. 1h30m, 2h, 45m or 1:30.")
    return (int(hours or 0) * 60 + int(minutes)) * 60


def _adjust(s, args, sign: int):
    user = s.users.get_by_code(args.code)
    seconds = sign * _parse_duration(args.amount)
    stats = s.attendance.adjust(user["id"], seconds, args.reason or "", via="cli")
    verb = "Added" if sign > 0 else "Subtracted"
    print(f"{verb} {timefmt.format_duration(abs(seconds))} for {stats.username} ({stats.code}).")
    print(f"New season total: {stats.total_text}")


def cmd_hours_add(s, args, config):
    _adjust(s, args, +1)


def cmd_hours_subtract(s, args, config):
    _adjust(s, args, -1)


def cmd_hours_history(s, args, config):
    rows = [[a["created_at"], a["code"], a["username"],
             ("+" if a["seconds"] > 0 else "-") + timefmt.format_duration(abs(a["seconds"])),
             a["created_via"], a["reason"]] for a in s.attendance.recent_adjustments(args.limit)]
    _table(rows, ["When", "ID", "Username", "Change", "From", "Reason"])


def cmd_import_legacy(s, args, config):
    from nfc_login.legacy.importer import (LegacyImporter, read_legacy,
                                           settings_from_legacy_config)
    if args.legacy_config:
        settings = settings_from_legacy_config(args.legacy_config)
    else:
        settings = {"host": args.host, "port": args.port, "user": args.user,
                    "password": args.password, "database": args.database}
        if settings["password"] is None:
            settings["password"] = getpass.getpass(f"Password for {args.user}@{args.host}: ")
    sections = [x.strip().upper() for x in args.sections.split(",")] if args.sections \
        else [sec["letter"] for sec in config.sections]
    unknown = set(sections) - set(s.users.section_names)
    if unknown:
        raise UserError(f"Unknown section(s): {', '.join(sorted(unknown))}")

    data = read_legacy(settings)
    print(f"Legacy database: {len(data['users'])} users, {len(data['pastseasons'])} "
          f"past-season rows, {len(data['records'])} records.")
    summary = LegacyImporter(s.db, sections).run(data, dry_run=args.dry_run)

    print("DRY RUN, nothing saved:" if args.dry_run else "Imported:")
    print(f"  users:        {summary.users} new, {summary.skipped_users} already imported")
    print(f"  cards:        {summary.cards} ({summary.cards_need_scan} match on first scan)")
    print(f"  hours:        {timefmt.format_duration(summary.hours_seconds)} into the "
          "current season")
    print(f"  signed in:    {summary.signed_in} (still signed in from the old system)")
    print(f"  past seasons: {', '.join(summary.seasons) or 'none'} "
          f"({summary.past_rows} totals)")
    print(f"  visit log:    {summary.records} records copied")
    if summary.created:
        print()
        _table([[legacy_id, code, name] for legacy_id, code, name in summary.created],
               ["Old ID", "New ID", "Name"])
    for warning in summary.warnings:
        print(f"Warning: {warning}")


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
    c = user.add_parser("add", help="create a user (gets the next free ID in the section)")
    c.add_argument("username")
    c.add_argument("--section", required=True, help="section letter, e.g. A")
    c.add_argument("--pin", action="store_true", help="also set a keypad PIN")
    c.set_defaults(func=cmd_user_add)
    c = user.add_parser("list", help="list users")
    c.add_argument("--all", action="store_true", help="include deactivated users")
    c.set_defaults(func=cmd_user_list)
    for name, func, helptext in [("show", cmd_user_show, "time, rank and last sign in/out"),
                                 ("deactivate", cmd_user_deactivate, "hide a user"),
                                 ("activate", cmd_user_activate, "un-hide a user")]:
        c = user.add_parser(name, help=helptext)
        c.add_argument("code", help="user ID, e.g. A07")
        c.set_defaults(func=func)
    c = user.add_parser("rename", help="change a username")
    c.add_argument("code", help="user ID, e.g. A07")
    c.add_argument("username")
    c.set_defaults(func=cmd_user_rename)
    c = user.add_parser("set-pin", help="set or clear a user's keypad PIN")
    c.add_argument("code", help="user ID, e.g. A07")
    c.add_argument("--clear", action="store_true")
    c.set_defaults(func=cmd_user_set_pin)

    tag = sub.add_parser("tag", help="manage NFC cards").add_subparsers(dest="action",
                                                                         required=True)
    c = tag.add_parser("enroll", help="link a card to a user")
    c.add_argument("code", help="user ID, e.g. A07")
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

    hours = sub.add_parser("hours", help="admin corrections to season time").add_subparsers(
        dest="action", required=True)
    for name, func, helptext in [("add", cmd_hours_add, "add time"),
                                 ("subtract", cmd_hours_subtract, "subtract time")]:
        c = hours.add_parser(name, help=helptext)
        c.add_argument("code", help="user ID, e.g. A07")
        c.add_argument("amount", help="e.g. 1h30m, 2h, 45m or 1:30")
        c.add_argument("--reason", help="shown in the history")
        c.set_defaults(func=func)
    c = hours.add_parser("history", help="recent corrections this season")
    c.add_argument("--limit", type=int, default=50)
    c.set_defaults(func=cmd_hours_history)

    c = sub.add_parser("import-legacy",
                       help="import users, cards and hours from the legacy attendance system")
    c.add_argument("--legacy-config", help="the legacy system's config.json (has its DB login)")
    c.add_argument("--host", default="localhost")
    c.add_argument("--port", type=int, default=3306)
    c.add_argument("--user", default="php", help="legacy DB user (default: php, read-only)")
    c.add_argument("--password", help="asked for if not given")
    c.add_argument("--database", default="attendance")
    c.add_argument("--sections", help="sections to put people in, in order, e.g. A or A,B "
                                      "(default: all, filling A first)")
    c.add_argument("--dry-run", action="store_true", help="show what would happen, save nothing")
    c.set_defaults(func=cmd_import_legacy)

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
    except (UserError, SeasonError, AttendanceError, ValueError, pymysql.err.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    return 0
