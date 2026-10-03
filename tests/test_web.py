import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

import pytest

from nfc_login.app import Services
from nfc_login.web.server import create_server


@pytest.fixture
def web(services, db):
    attendance, seasons, users = services
    users.set_admin_pin("2468")
    server = create_server(Services(db, attendance, seasons, users), users.sections,
                           "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", users, attendance
    server.shutdown()


def opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def post(op, url, **fields):
    data = urllib.parse.urlencode(fields).encode()
    return op.open(urllib.request.Request(url, data=data))


def test_live_status_lists_who_is_here(web, clock):
    base, users, attendance = web
    taylor = users.add("taylor", "A")
    users.add("dee", "D")
    attendance.toggle(taylor["id"], "card")
    status = json.load(urllib.request.urlopen(base + "/api/status"))
    assert [(p["code"], p["team"]) for p in status["here"]] == [("A001", "Robot")]
    assert [s["name"] for s in status["sections"]] == [
        "Robot", "Impact", "Sustainability", "Strategy", "Mentors"]
    assert {(r["code"], r["team"]) for r in status["leaderboard"]} == {
        ("A001", "Robot"), ("D001", "Strategy")}
    page = urllib.request.urlopen(base + "/").read().decode()
    assert "Who's here" in page and "{{REFRESH_MS}}" not in page


def test_admin_requires_pin_then_adjusts_and_adds(web):
    base, users, attendance = web
    taylor = users.add("taylor", "A")
    op = opener()
    with pytest.raises(urllib.error.HTTPError) as err:
        post(op, base + "/admin/login", pin="0000")
    assert err.value.code == 401
    # Not logged in: actions bounce back to the login page and change nothing.
    page = post(op, base + "/admin/adjust", code="A001", direction="add", hours=5,
                minutes=0).read().decode()
    assert "Admin PIN" in page and attendance.user_stats(taylor["id"]).total_seconds == 0

    assert "Add or subtract hours" in post(op, base + "/admin/login", pin="2468").read().decode()
    page = post(op, base + "/admin/adjust", code="A001", direction="add", hours=2, minutes=15,
                reason="<b>workshop</b>").read().decode()
    assert "Added 2h 15m to taylor (A001)" in page
    assert "&lt;b&gt;workshop&lt;/b&gt;" in page
    assert attendance.user_stats(taylor["id"]).total_seconds == 135 * 60

    page = post(op, base + "/admin/adjust", code="A001", direction="subtract", hours=9,
                minutes=0).read().decode()
    assert "only has 2h 15m" in page

    page = post(op, base + "/admin/users", username="Bea", section="B", pin="").read().decode()
    assert "Created Bea with ID B001" in page
    assert users.get_by_code("B001")["username"] == "Bea"

    page = post(op, base + "/admin/team", code="B001", section="M").read().decode()
    assert "Bea is now in Mentors with ID 001." in page
    assert users.get_by_code("001")["username"] == "Bea"


def test_cross_site_post_rejected(web):
    base, _users, _attendance = web
    req = urllib.request.Request(base + "/admin/login", data=b"pin=2468",
                                 headers={"Origin": "http://evil.example"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 403


def test_changing_the_admin_pin_logs_out_the_web_page(web):
    base, users, _ = web
    op = opener()
    assert "Add or subtract hours" in post(op, base + "/admin/login", pin="2468").read().decode()
    assert "Add or subtract hours" in op.open(base + "/admin").read().decode()
    users.set_admin_pin("1357")           # e.g. on the kiosk: System > 4
    assert "Admin PIN" in op.open(base + "/admin").read().decode()
