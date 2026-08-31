"""Tests for server/leaderboard.py -- GET /api/leaderboard, PUT
/api/leaderboard/opt, PUT /api/leaderboard/name, and the weekly-total
recompute wired into PUT /api/state.

Leaderboard entries used to show a per-week HMAC-derived alias; the owner
asked for real, user-chosen public names instead ("Proper names should be
seen on leaderboard -- not fake names"). See server/leaderboard.py's module
docstring for the full design: `users.public_name` is a separate, never
auto-populated column, and a user only appears on the leaderboard once they
explicitly set one via PUT /api/leaderboard/name.
"""
import datetime
import json
import re

import pytest

from server.leaderboard import (
    MAX_WEEK_SECONDS,
    compute_week_seconds,
    current_week_start,
    validate_public_name,
    week_start_for,
)

JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def register_verify(client, outbox, email, password="pw123456", display_name=None):
    body = {"email": email, "password": password}
    if display_name:
        body["display_name"] = display_name
    client.post(
        "/api/auth/register",
        data=json.dumps(body),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    match = re.search(r"token=([A-Za-z0-9_\-]+)", outbox[-1]["text"])
    resp = client.get("/verify?token={}".format(match.group(1)))
    assert resp.status_code == 302  # auto-login


def put_state(client, sessions, version=0):
    payload = {"sessions": sessions}
    return client.put(
        "/api/state",
        data=json.dumps({"payload": payload, "version": version}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )


def set_name(client, name):
    return client.put(
        "/api/leaderboard/name",
        data=json.dumps({"public_name": name}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )


def session_today(seconds, hour=10):
    today = current_week_start() + datetime.timedelta(days=1)  # a Tuesday, safely mid-week
    return {
        "date": today.isoformat(),
        "course": "Math",
        "type": "Stopwatch",
        "durationSeconds": seconds,
        "timestamp": "10:00 AM",
        "hourOfDayExecuted": hour,
    }


# --------------------------------------------------------------------- auth

def test_leaderboard_requires_login(client):
    resp = client.get("/api/leaderboard", headers=JSON_HEADERS)
    assert resp.status_code == 401

    resp = client.put(
        "/api/leaderboard/opt",
        data=json.dumps({"opted_in": False}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 401

    resp = client.put(
        "/api/leaderboard/name",
        data=json.dumps({"public_name": "Bob"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 401


def test_opt_requires_csrf_header(client, outbox):
    register_verify(client, outbox, "csrf@example.com")
    resp = client.put(
        "/api/leaderboard/opt",
        data=json.dumps({"opted_in": False}),
        content_type="application/json",
    )
    assert resp.status_code == 403


def test_opt_validates_body(client, outbox):
    register_verify(client, outbox, "optval@example.com")
    resp = client.put(
        "/api/leaderboard/opt",
        data=json.dumps({"opted_in": "yes"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_name_requires_csrf_header(client, outbox):
    register_verify(client, outbox, "namecsrf@example.com")
    resp = client.put(
        "/api/leaderboard/name",
        data=json.dumps({"public_name": "Bob"}),
        content_type="application/json",
    )
    assert resp.status_code == 403


def test_name_requires_json_object_body(client, outbox):
    register_verify(client, outbox, "namebody@example.com")
    resp = client.put(
        "/api/leaderboard/name",
        data="not json",
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


# ------------------------------------------------------------------ flow

def test_new_user_defaults_opted_in_zero_seconds_no_name(client, outbox):
    register_verify(client, outbox, "fresh@example.com")
    resp = client.get("/api/leaderboard", headers=JSON_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["you"]["opted_in"] is True
    assert data["you"]["seconds"] == 0
    assert data["you"]["rank"] is None  # nothing logged yet -> unranked
    assert data["you"]["public_name"] is None
    assert data["entries"] == []


def test_user_without_public_name_is_not_listed_even_with_seconds(client, outbox):
    """The core of this change: seconds logged is not enough to appear on
    the board -- a public name must be explicitly set."""
    register_verify(client, outbox, "noname@example.com")
    put_state(client, [session_today(3600)])

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb["you"]["seconds"] == 3600  # their own total is still visible to them
    assert lb["you"]["public_name"] is None
    assert lb["you"]["rank"] is None  # not listed -> unranked
    assert lb["entries"] == []
    assert lb["participants"] == 0


def test_setting_a_name_makes_the_user_appear(client, outbox):
    register_verify(client, outbox, "named@example.com")
    put_state(client, [session_today(3600)])

    name_resp = set_name(client, "River Blue")
    assert name_resp.status_code == 200
    assert name_resp.get_json() == {"ok": True, "public_name": "River Blue"}

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb["you"]["public_name"] == "River Blue"
    assert lb["you"]["rank"] == 1
    assert lb["participants"] == 1
    assert lb["entries"] == [{"rank": 1, "name": "River Blue", "seconds": 3600}]


def test_state_put_recomputes_weekly_total(client, outbox):
    register_verify(client, outbox, "studier@example.com")
    set_name(client, "Studier")
    resp = put_state(client, [session_today(1800), session_today(900)])
    assert resp.status_code == 200

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb["you"]["seconds"] == 2700
    assert lb["you"]["rank"] == 1
    assert lb["participants"] == 1
    assert len(lb["entries"]) == 1
    assert lb["entries"][0] == {"rank": 1, "name": "Studier", "seconds": 2700}


def test_weekly_total_is_recomputed_wholesale_not_accumulated(client, outbox):
    """A second PUT with fewer/shorter sessions must REPLACE the total, not
    add to it -- the spec says "recomputed on every PUT /api/state from the
    sessions in the payload", not incremented."""
    register_verify(client, outbox, "recompute@example.com")
    put_state(client, [session_today(3600)])
    lb1 = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb1["you"]["seconds"] == 3600

    put_state(client, [session_today(100)], version=1)
    lb2 = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb2["you"]["seconds"] == 100


def test_sessions_outside_current_week_are_excluded(client, outbox):
    register_verify(client, outbox, "outside@example.com")
    old_session = {
        "date": "2000-01-01",
        "course": "History",
        "type": "Stopwatch",
        "durationSeconds": 5000,
        "timestamp": "1:00 PM",
        "hourOfDayExecuted": 13,
    }
    resp = put_state(client, [old_session, session_today(60)])
    assert resp.status_code == 200

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb["you"]["seconds"] == 60  # only the in-week session counts


def test_opt_out_removes_from_entries_and_participants(client, outbox):
    register_verify(client, outbox, "optout@example.com")
    set_name(client, "OptOuter")
    put_state(client, [session_today(600)])
    before = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert before["participants"] == 1
    assert len(before["entries"]) == 1

    opt_resp = client.put(
        "/api/leaderboard/opt",
        data=json.dumps({"opted_in": False}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert opt_resp.status_code == 200

    after = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert after["you"]["opted_in"] is False
    assert after["you"]["rank"] is None
    assert after["participants"] == 0
    assert after["entries"] == []
    # Their own seconds total (and name) are still visible to them while opted out.
    assert after["you"]["seconds"] == 600
    assert after["you"]["public_name"] == "OptOuter"


def test_opt_back_in_restores_entry_without_resetting_seconds(client, outbox):
    register_verify(client, outbox, "optback@example.com")
    set_name(client, "OptBacker")
    put_state(client, [session_today(1200)])
    client.put(
        "/api/leaderboard/opt",
        data=json.dumps({"opted_in": False}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    client.put(
        "/api/leaderboard/opt",
        data=json.dumps({"opted_in": True}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb["you"]["opted_in"] is True
    assert lb["you"]["seconds"] == 1200  # untouched by the opt toggle
    assert lb["you"]["rank"] == 1
    assert len(lb["entries"]) == 1


def test_name_can_have_opted_in_stay_independent(client, outbox):
    """The existing opted_in flag keeps working independently of a name: a
    user can have a name set and still opt out (covered above), and a user
    can opt out before ever setting a name at all."""
    register_verify(client, outbox, "independent@example.com")
    put_state(client, [session_today(100)])
    client.put(
        "/api/leaderboard/opt",
        data=json.dumps({"opted_in": False}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    set_name(client, "LateNamer")

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb["you"]["public_name"] == "LateNamer"
    assert lb["you"]["opted_in"] is False
    assert lb["you"]["rank"] is None  # opted out -> still not listed
    assert lb["entries"] == []


def test_ranking_and_top_20_ordering_across_users(client, outbox):
    totals = [5000, 3000, 4000]
    names = ["Rank Alpha", "Rank Beta", "Rank Gamma"]
    emails = ["rank-a@example.com", "rank-b@example.com", "rank-c@example.com"]
    for email, name, secs in zip(emails, names, totals):
        register_verify(client, outbox, email)
        set_name(client, name)
        put_state(client, [session_today(secs)])
        client.post("/api/auth/logout", headers=JSON_HEADERS)

    # Re-login as rank-a to read the board (email already verified from the
    # loop above; no need to register again).
    resp = client.post(
        "/api/auth/login",
        data=json.dumps({"email": emails[0], "password": "pw123456"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 200

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert [e["seconds"] for e in lb["entries"]] == [5000, 4000, 3000]
    assert [e["name"] for e in lb["entries"]] == ["Rank Alpha", "Rank Gamma", "Rank Beta"]
    assert [e["rank"] for e in lb["entries"]] == [1, 2, 3]
    assert lb["participants"] == 3
    assert lb["you"]["rank"] == 1
    assert lb["you"]["seconds"] == 5000


def test_unnamed_user_does_not_dilute_ranking_of_named_users(client, outbox):
    register_verify(client, outbox, "unnamed-a@example.com")
    put_state(client, [session_today(9999)])  # highest total, but never names themself
    client.post("/api/auth/logout", headers=JSON_HEADERS)

    register_verify(client, outbox, "named-b@example.com")
    set_name(client, "Second Place")
    put_state(client, [session_today(50)])

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb["participants"] == 1
    assert lb["entries"] == [{"rank": 1, "name": "Second Place", "seconds": 50}]


# ---------------------------------------------------------- name validation

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Bob", "Bob"),
        ("Ok", "Ok"),
        ("J.R.", "J.R."),
        ("  River   Blue  ", "River Blue"),  # whitespace collapsed + trimmed
        ("A" * 24, "A" * 24),  # exactly at the max
    ],
)
def test_name_validation_accepts_valid_names(client, outbox, raw, expected):
    register_verify(client, outbox, "valid-{}@example.com".format(abs(hash(raw))))
    resp = set_name(client, raw)
    assert resp.status_code == 200
    assert resp.get_json()["public_name"] == expected


@pytest.mark.parametrize(
    "raw,reason",
    [
        ("A", "too short"),
        ("A" * 25, "too long"),
        ("", "empty"),
        ("   ", "whitespace only"),
        ("!!!---***", "punctuation only"),
        (123, "not a string"),
        (None, "not a string"),
        (True, "not a string"),
        (["Bob"], "not a string"),
    ],
)
def test_name_validation_rejects_shape_violations(client, outbox, raw, reason):
    register_verify(client, outbox, "invalid-{}@example.com".format(abs(hash(reason) + hash(str(raw)))))
    resp = set_name(client, raw)
    assert resp.status_code == 400, reason
    assert resp.get_json()["error"] == "validation_error"


def test_name_validation_rejects_email_looking_names(client, outbox):
    register_verify(client, outbox, "emailname@example.com")
    resp = set_name(client, "bob@example.com")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


@pytest.mark.parametrize("raw", ["www.foo.com", "http://x.io", "https://evil.test/path"])
def test_name_validation_rejects_url_looking_names(client, outbox, raw):
    register_verify(client, outbox, "urlname-{}@example.com".format(abs(hash(raw))))
    resp = set_name(client, raw)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_name_validation_rejects_script_payload(client, outbox):
    """The value is rendered into other users' browsers -- it must be safe
    by construction here (in addition to being escaped at render time on the
    frontend). `<` / `>` are rejected outright so a name can never become an
    HTML tag no matter what a future rendering bug does with it."""
    register_verify(client, outbox, "xssname@example.com")
    resp = set_name(client, "<script>")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "validation_error"

    # Even a payload that would fit the length window is rejected, and never
    # silently stored or echoed back unescaped.
    resp2 = set_name(client, "<b>hi</b>")
    assert resp2.status_code == 400
    assert "<script>" not in resp2.get_data(as_text=True)
    assert "<b>" not in resp2.get_data(as_text=True)


def test_name_validation_strips_control_and_zero_width_characters(client, outbox):
    register_verify(client, outbox, "ctrlname@example.com")
    # A newline plus a zero-width space (U+200B) hidden inside an otherwise
    # normal name -- both must be stripped, not merely rejected, and the
    # cleaned result must still pass the length check.
    raw = "Bo\u200bb\nSmith"
    resp = set_name(client, raw)
    assert resp.status_code == 200
    assert resp.get_json()["public_name"] == "BobSmith"


def test_name_validation_normalizes_unicode_nfkc(client, outbox):
    register_verify(client, outbox, "nfkcname@example.com")
    # U+FF22 U+FF4F U+FF42 = fullwidth "Bob" -- NFKC folds these to ASCII "Bob".
    raw = "Ｂｏｂ"
    resp = set_name(client, raw)
    assert resp.status_code == 200
    assert resp.get_json()["public_name"] == "Bob"


def test_name_uniqueness_is_case_insensitive(client, outbox):
    register_verify(client, outbox, "taken-a@example.com")
    resp1 = set_name(client, "Alice")
    assert resp1.status_code == 200
    client.post("/api/auth/logout", headers=JSON_HEADERS)

    register_verify(client, outbox, "taken-b@example.com")
    resp2 = set_name(client, "alice")
    assert resp2.status_code == 409
    assert resp2.get_json()["error"] == "name_taken"

    resp3 = set_name(client, "  ALICE  ")
    assert resp3.status_code == 409
    assert resp3.get_json()["error"] == "name_taken"


def test_user_can_change_their_own_name_freely(client, outbox):
    register_verify(client, outbox, "rename@example.com")
    set_name(client, "First Name")
    resp = set_name(client, "Second Name")
    assert resp.status_code == 200
    assert resp.get_json()["public_name"] == "Second Name"

    resp2 = set_name(client, "second name")  # re-claiming their own name, different case
    assert resp2.status_code == 200
    assert resp2.get_json()["public_name"] == "second name"


# -------------------------------------------------------------- anonymity

def test_response_exposes_only_rank_name_seconds_in_entries(client, outbox):
    for i in range(3):
        register_verify(client, outbox, "anon{}@example.com".format(i))
        set_name(client, "Anon Runner {}".format(i))
        put_state(client, [session_today(1000 + i)])
        client.post("/api/auth/logout", headers=JSON_HEADERS)

    register_verify(client, outbox, "anon-reader@example.com")
    set_name(client, "Anon Reader")
    put_state(client, [session_today(1)])
    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()

    assert set(lb.keys()) == {"week_start", "you", "entries", "participants"}
    assert set(lb["you"].keys()) == {"rank", "seconds", "public_name", "opted_in"}
    for entry in lb["entries"]:
        assert set(entry.keys()) == {"rank", "name", "seconds"}


def test_no_pii_beyond_the_chosen_public_name(client, outbox):
    """The core guarantee, adapted for named entries: email, numeric id,
    display_name and timestamps must never appear in the response body --
    the *public_name* itself legitimately appears now (that's the whole
    point of this change), but nothing else identifying does."""
    email = "super-secret-leaktest@example.com"
    display_name = "TotallyIdentifiableRealName"
    register_verify(client, outbox, email, display_name=display_name)
    set_name(client, "Leak Tester")
    put_state(client, [session_today(4242)])

    resp = client.get("/api/leaderboard", headers=JSON_HEADERS)
    raw_text = resp.get_data(as_text=True)

    assert email not in raw_text
    assert email.split("@")[0] not in raw_text
    assert display_name not in raw_text
    assert "@example.com" not in raw_text

    data = resp.get_json()
    # No key anywhere in the payload should be one of these.
    forbidden_keys = {"email", "id", "user_id", "display_name", "created_at", "updated_at", "timestamp"}

    def walk(node):
        if isinstance(node, dict):
            assert forbidden_keys.isdisjoint(node.keys()), node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)


# -------------------------------------------------------- hostile payloads

def test_hostile_payload_cannot_crash_or_inflate_the_leaderboard(client, outbox):
    register_verify(client, outbox, "hostile@example.com")
    set_name(client, "Hostile Tester")
    today = (current_week_start() + datetime.timedelta(days=1)).isoformat()

    hostile_sessions = [
        "not-a-dict",
        123,
        None,
        {"date": "not-a-date", "durationSeconds": 100},
        {"date": today},  # missing durationSeconds
        {"date": today, "durationSeconds": "a lot"},  # wrong type
        {"date": today, "durationSeconds": True},  # bool, not a real number
        {"date": today, "durationSeconds": -500},  # negative
        {"date": today, "durationSeconds": float("nan")},  # NaN
        {"date": today, "durationSeconds": float("inf")},  # Infinity
        {"date": today, "durationSeconds": 10**9},  # absurdly large, single entry
        {"date": today, "durationSeconds": 42},  # one legitimate entry
    ]

    resp = put_state(client, hostile_sessions)
    assert resp.status_code == 200  # must not 500

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    # Only the one legitimate 42s entry should have counted; the 10**9
    # entry is a well-formed number so it WOULD count on its own, but the
    # weekly total must be clamped to MAX_WEEK_SECONDS regardless.
    assert lb["you"]["seconds"] <= MAX_WEEK_SECONDS
    assert lb["you"]["seconds"] == MAX_WEEK_SECONDS  # the 10**9 entry alone blows past the cap


def test_hostile_payload_sessions_not_a_list(client, outbox):
    register_verify(client, outbox, "hostile2@example.com")
    resp = client.put(
        "/api/state",
        data=json.dumps({"payload": {"sessions": "not-a-list"}, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 200

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb["you"]["seconds"] == 0


def test_hostile_payload_missing_sessions_key(client, outbox):
    register_verify(client, outbox, "hostile3@example.com")
    resp = client.put(
        "/api/state",
        data=json.dumps({"payload": {"profile": {"dailyGoalHours": 4}}, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 200

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb["you"]["seconds"] == 0


def test_compute_week_seconds_clamps_large_legitimate_totals(app):
    with app.app_context():
        week_start = current_week_start()
        in_week_date = (week_start + datetime.timedelta(days=2)).isoformat()
        sessions = [
            {"date": in_week_date, "durationSeconds": MAX_WEEK_SECONDS}
            for _ in range(5)
        ]
        total = compute_week_seconds({"sessions": sessions}, week_start)
        assert total == MAX_WEEK_SECONDS


def test_week_start_for_is_the_monday():
    # 2026-01-05 is a Monday; the whole week (Mon..Sun) must map to it.
    monday = datetime.date(2026, 1, 5)
    for offset in range(7):
        d = monday + datetime.timedelta(days=offset)
        assert week_start_for(d) == monday


# ------------------------------------------------------------ unit-level

def test_validate_public_name_unit():
    assert validate_public_name("Bob") == ("Bob", None)
    assert validate_public_name("  Bob   Smith  ") == ("Bob Smith", None)
    cleaned, err = validate_public_name("A")
    assert cleaned is None and err
    cleaned, err = validate_public_name("bob@example.com")
    assert cleaned is None and err
    cleaned, err = validate_public_name("<script>")
    assert cleaned is None and err
    cleaned, err = validate_public_name(None)
    assert cleaned is None and err


# ------------------------------------------------------ local-date week bug
#
# Sessions are stored with the CLIENT's local calendar date (see index.html's
# getLocalDateStr), but a naive "current week" is whatever the SERVER's own
# UTC clock says. For a user meaningfully ahead of UTC (e.g. IST, UTC+5:30),
# their local calendar date rolls over to a new day -- and sometimes a new
# ISO week -- while the server's UTC clock is still on the previous one, so a
# session dated by their own "today" can land outside the week window the
# server thinks is "current" and silently vanish from the weekly total until
# the server's UTC date catches up. These tests pin that fix: a client-
# reported `local_date` (bounded to +-1 day of the server's own UTC date,
# the maximum any real IANA timezone offset could ever cause) determines
# which week is "current" instead of blindly trusting server-side UTC "now".

def _sunday_and_monday():
    """A real (Sunday, Monday) pair -- computed, not hardcoded, so the test
    doesn't depend on which day some fixed date happens to fall on."""
    base = datetime.date(2024, 6, 1)
    sunday = base + datetime.timedelta(days=(6 - base.weekday()) % 7)
    monday = sunday + datetime.timedelta(days=1)
    return sunday, monday


def test_parse_client_local_date_bounds(monkeypatch):
    from server import leaderboard

    fake_now = datetime.datetime(2024, 6, 5, 12, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(leaderboard, "utcnow", lambda: fake_now)

    # Within the +-1 day window a real timezone offset could cause: trusted.
    assert leaderboard._parse_client_local_date("2024-06-04") == datetime.date(2024, 6, 4)
    assert leaderboard._parse_client_local_date("2024-06-06") == datetime.date(2024, 6, 6)
    assert leaderboard._parse_client_local_date("2024-06-05") == datetime.date(2024, 6, 5)

    # Further than any real timezone offset could explain: ignored, not trusted
    # (guards against a client steering its own total into a friendlier week).
    assert leaderboard._parse_client_local_date("2024-06-03") is None
    assert leaderboard._parse_client_local_date("2024-06-07") is None

    # Malformed/missing input never raises -- just falls back.
    assert leaderboard._parse_client_local_date(None) is None
    assert leaderboard._parse_client_local_date(123) is None
    assert leaderboard._parse_client_local_date("") is None
    assert leaderboard._parse_client_local_date("not-a-date") is None


def test_current_week_start_uses_client_local_date_near_week_boundary(monkeypatch):
    from server import leaderboard

    sunday, monday = _sunday_and_monday()
    fake_now = datetime.datetime(sunday.year, sunday.month, sunday.day, 23, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(leaderboard, "utcnow", lambda: fake_now)

    # No local_date -> falls back to the server's own UTC "today" (still
    # Sunday), so "this week" is the week containing that Sunday.
    assert leaderboard.current_week_start() == leaderboard.week_start_for(sunday)

    # The client's local calendar date has already rolled over to Monday
    # (e.g. a user in UTC+5:30 just after their local midnight) -- "this
    # week" must be the NEW week starting that Monday, not the old one.
    assert leaderboard.current_week_start(monday.isoformat()) == monday


def test_current_week_start_ignores_implausible_local_date(monkeypatch):
    from server import leaderboard

    fake_now = datetime.datetime(2024, 6, 5, 12, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(leaderboard, "utcnow", lambda: fake_now)
    far_future = "2024-07-01"
    assert leaderboard.current_week_start(far_future) == leaderboard.current_week_start(None)


def test_state_put_and_leaderboard_agree_on_local_date_near_week_boundary(client, outbox, monkeypatch):
    """End-to-end: a session timestamped with the client's local "Monday"
    while the server's own UTC clock still reads "Sunday night" must be
    counted in the Monday-anchored week when the client consistently reports
    its local date on both the write (PUT /api/state) and the read (GET
    /api/leaderboard) -- this is the exact bug the owner reported as "logged
    but missing from the leaderboard" for anyone not on UTC."""
    from server import leaderboard

    register_verify(client, outbox, "boundary@example.com")
    set_name(client, "BoundaryUser")

    sunday, monday = _sunday_and_monday()
    fake_now = datetime.datetime(sunday.year, sunday.month, sunday.day, 23, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(leaderboard, "utcnow", lambda: fake_now)

    session = {
        "date": monday.isoformat(),
        "course": "Math",
        "type": "Stopwatch",
        "durationSeconds": 600,
        "timestamp": "11:00 PM",
        "hourOfDayExecuted": 23,
    }
    put_resp = client.put(
        "/api/state",
        data=json.dumps({"payload": {"sessions": [session]}, "version": 0, "local_date": monday.isoformat()}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert put_resp.status_code == 200

    lb_local = client.get(
        "/api/leaderboard?local_date={}".format(monday.isoformat()), headers=JSON_HEADERS
    )
    assert lb_local.get_json()["you"]["seconds"] == 600

    # Reading with no local_date falls back to the server's raw UTC "today"
    # (still Sunday) -- a different, also-legitimate week bucket that this
    # Monday-dated session correctly does not belong to.
    lb_utc = client.get("/api/leaderboard", headers=JSON_HEADERS)
    assert lb_utc.get_json()["you"]["seconds"] == 0


def test_opt_uses_local_date_for_week_bucket(client, outbox, monkeypatch):
    """PUT /api/leaderboard/opt must bucket the opt-in choice into the SAME
    week a same-local-date state sync / leaderboard read would use -- else a
    user near a week boundary could opt out of the week their session totals
    are actually landing in, and see their choice silently not take effect."""
    from server import leaderboard

    register_verify(client, outbox, "optlocal@example.com")

    sunday, monday = _sunday_and_monday()
    fake_now = datetime.datetime(sunday.year, sunday.month, sunday.day, 23, 30, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(leaderboard, "utcnow", lambda: fake_now)

    resp = client.put(
        "/api/leaderboard/opt",
        data=json.dumps({"opted_in": False, "local_date": monday.isoformat()}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 200

    lb = client.get("/api/leaderboard?local_date={}".format(monday.isoformat()), headers=JSON_HEADERS)
    assert lb.get_json()["you"]["opted_in"] is False
