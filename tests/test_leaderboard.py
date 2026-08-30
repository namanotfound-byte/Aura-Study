"""Tests for server/leaderboard.py -- GET /api/leaderboard, PUT
/api/leaderboard/opt, and the weekly-total recompute wired into
PUT /api/state. See SPEC-PHASE4.md "Leaderboard API" / "Anonymity rules" /
"Leaderboard data".
"""
import datetime
import json
import re

import pytest

from server.leaderboard import (
    MAX_WEEK_SECONDS,
    compute_week_seconds,
    current_week_start,
    generate_alias,
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


# ------------------------------------------------------------------ flow

def test_new_user_defaults_opted_in_with_zero_seconds(client, outbox):
    register_verify(client, outbox, "fresh@example.com")
    resp = client.get("/api/leaderboard", headers=JSON_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["you"]["opted_in"] is True
    assert data["you"]["seconds"] == 0
    assert data["you"]["rank"] is None  # nothing logged yet -> unranked
    assert data["entries"] == []
    assert isinstance(data["you"]["alias"], str) and data["you"]["alias"]


def test_state_put_recomputes_weekly_total(client, outbox):
    register_verify(client, outbox, "studier@example.com")
    resp = put_state(client, [session_today(1800), session_today(900)])
    assert resp.status_code == 200

    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()
    assert lb["you"]["seconds"] == 2700
    assert lb["you"]["rank"] == 1
    assert lb["participants"] == 1
    assert len(lb["entries"]) == 1
    assert lb["entries"][0] == {
        "rank": 1,
        "alias": lb["you"]["alias"],
        "seconds": 2700,
    }


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
    # Their own seconds total is still visible to them even while opted out.
    assert after["you"]["seconds"] == 600


def test_opt_back_in_restores_entry_without_resetting_seconds(client, outbox):
    register_verify(client, outbox, "optback@example.com")
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


def test_ranking_and_top_20_ordering_across_users(client, outbox):
    totals = [5000, 3000, 4000]
    emails = ["rank-a@example.com", "rank-b@example.com", "rank-c@example.com"]
    for email, secs in zip(emails, totals):
        register_verify(client, outbox, email)
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
    assert [e["rank"] for e in lb["entries"]] == [1, 2, 3]
    assert lb["participants"] == 3
    assert lb["you"]["rank"] == 1
    assert lb["you"]["seconds"] == 5000


# ------------------------------------------------------------- anonymity

def test_response_exposes_only_rank_alias_seconds_in_entries(client, outbox):
    for i in range(3):
        register_verify(client, outbox, "anon{}@example.com".format(i))
        put_state(client, [session_today(1000 + i)])
        client.post("/api/auth/logout", headers=JSON_HEADERS)

    register_verify(client, outbox, "anon-reader@example.com")
    put_state(client, [session_today(1)])
    lb = client.get("/api/leaderboard", headers=JSON_HEADERS).get_json()

    assert set(lb.keys()) == {"week_start", "you", "entries", "participants"}
    assert set(lb["you"].keys()) == {"rank", "seconds", "alias", "opted_in"}
    for entry in lb["entries"]:
        assert set(entry.keys()) == {"rank", "alias", "seconds"}


def test_no_pii_anywhere_in_the_leaderboard_response(client, outbox):
    """The core anonymity guarantee: nothing that could identify or
    correlate a user -- email, numeric id, display name, or a timestamp --
    ever appears in the response body, serialized or not."""
    email = "super-secret-leaktest@example.com"
    display_name = "TotallyIdentifiableRealName"
    register_verify(client, outbox, email, display_name=display_name)
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


def test_alias_rotates_weekly_and_is_not_reversible_looking(app):
    """alias = HMAC-SHA256(SECRET_KEY, "leaderboard:<user_id>:<week>") --
    different weeks for the same user must produce different aliases (no
    cross-week correlation), and the alias must not literally contain the
    user id."""
    with app.app_context():
        alias_week1 = generate_alias(42, "2026-01-05")
        alias_week2 = generate_alias(42, "2026-01-12")
        assert alias_week1 != alias_week2
        assert "42" not in alias_week1
        assert "42" not in alias_week2

        # Deterministic within the same user+week (so "you" can find
        # themselves in `entries` by comparing aliases across two calls).
        assert generate_alias(42, "2026-01-05") == alias_week1


def test_different_users_get_different_aliases_same_week(app):
    with app.app_context():
        a = generate_alias(1, "2026-01-05")
        b = generate_alias(2, "2026-01-05")
        assert a != b


# -------------------------------------------------------- hostile payloads

def test_hostile_payload_cannot_crash_or_inflate_the_leaderboard(client, outbox):
    register_verify(client, outbox, "hostile@example.com")
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
