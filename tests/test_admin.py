"""Tests for server/admin.py and the owner-only /admin/users,
/admin/users/<id>/add-time and /admin/actions routes in server/app.py.

Uses the `client`/`app`/`outbox` fixtures from tests/conftest.py, and the
same `owner_email` fixture override pattern tests/test_spotify_requests.py
uses for its TestAdminGate class. Runs against both database backends (see
conftest's `backend` fixture).
"""
import json

import pytest

from server import admin
from server.db import get_db
from server.leaderboard import current_week_start

JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def register_and_login(client, email="user@example.com", password="hunter2pw"):
    client.post(
        "/api/auth/register",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    lookup_email = email.strip().lower()
    with client.application.app_context():
        db = get_db()
        db.execute("UPDATE users SET is_verified = %s WHERE email = %s", (True, lookup_email))
        db.commit()
        user_id = db.execute("SELECT id FROM users WHERE email = %s", (lookup_email,)).fetchone()["id"]
    login_resp = client.post(
        "/api/auth/login",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert login_resp.status_code == 200, login_resp.get_data(as_text=True)
    return user_id


def add_time(client, user_id, minutes=45, date=None, course="Math", reason="timer crashed"):
    if date is None:
        date = current_week_start().isoformat()
    return client.post(
        "/admin/users/{}/add-time".format(user_id),
        data={"minutes": str(minutes), "date": date, "course": course, "reason": reason},
        headers=JSON_HEADERS,
    )


def get_state(client):
    return client.get("/api/state").get_json()


def put_state(client, payload, version):
    return client.put(
        "/api/state",
        data=json.dumps({"payload": payload, "version": version}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )


# ---------------------------------------------------------------------------
# Validators (pure functions, no DB)
# ---------------------------------------------------------------------------

def test_validate_minutes_accepts_positive_integers():
    assert admin.validate_minutes("45") == (45, None)
    assert admin.validate_minutes(45) == (45, None)


@pytest.mark.parametrize("raw", ["0", "-5", "not-a-number", "12.5", "", None, True])
def test_validate_minutes_rejects_bad_values(raw):
    minutes, err = admin.validate_minutes(raw)
    assert minutes is None
    assert err is not None


def test_validate_minutes_rejects_over_24_hours():
    minutes, err = admin.validate_minutes(str(admin.MAX_MINUTES + 1))
    assert minutes is None
    assert "24 hours" in err


def test_validate_minutes_accepts_exactly_24_hours():
    assert admin.validate_minutes(str(admin.MAX_MINUTES)) == (admin.MAX_MINUTES, None)


def test_validate_date_rejects_garbage():
    value, err = admin.validate_date("not-a-date")
    assert value is None and err is not None


def test_validate_date_rejects_future():
    from server.db import utcnow
    import datetime

    future = (utcnow().date() + datetime.timedelta(days=1)).isoformat()
    value, err = admin.validate_date(future)
    assert value is None
    assert "future" in err


def test_validate_date_accepts_today():
    from server.db import utcnow

    today = utcnow().date().isoformat()
    assert admin.validate_date(today) == (today, None)


def test_validate_course_rejects_empty_and_too_long():
    assert admin.validate_course("")[0] is None
    assert admin.validate_course("   ")[0] is None
    assert admin.validate_course("x" * (admin.MAX_COURSE_LENGTH + 1))[0] is None
    assert admin.validate_course("Math")[0] == "Math"


def test_validate_reason_rejects_empty_and_too_long():
    assert admin.validate_reason("")[0] is None
    assert admin.validate_reason("x" * (admin.MAX_REASON_LENGTH + 1))[0] is None
    assert admin.validate_reason("forgot to start timer")[0] == "forgot to start timer"


# ---------------------------------------------------------------------------
# Access control matrix
# ---------------------------------------------------------------------------

ADMIN_GET_PATHS = ["/admin", "/admin/users", "/admin/support", "/admin/actions"]


@pytest.mark.parametrize("path", ADMIN_GET_PATHS)
def test_admin_pages_anonymous_visitor_is_redirected(client, path):
    resp = client.get(path)
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/login")


@pytest.mark.parametrize("path", ADMIN_GET_PATHS)
def test_admin_pages_unreachable_when_owner_email_unset(client, path, outbox):
    register_and_login(client, email="someone@example.com")
    resp = client.get(path)
    assert resp.status_code == 404


def test_add_time_anonymous_is_redirected(client):
    resp = add_time(client, 1)
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/login")


def test_add_time_unreachable_when_owner_email_unset(client, app, outbox):
    target_id = register_and_login(client, email="target@example.com")
    resp = add_time(client, target_id)
    assert resp.status_code == 404


class TestAdminGate:
    """Owner-gated admin route tests, run with OWNER_EMAIL actually set."""

    @pytest.fixture
    def owner_email(self):
        return "owner@example.com"

    @pytest.mark.parametrize("path", ADMIN_GET_PATHS)
    def test_non_owner_gets_404_not_403(self, client, app, outbox, path):
        register_and_login(client, email="not-the-owner@example.com")
        resp = client.get(path)
        assert resp.status_code == 404

    @pytest.mark.parametrize("path", ADMIN_GET_PATHS)
    def test_owner_gets_200(self, client, app, outbox, path):
        register_and_login(client, email="owner@example.com")
        resp = client.get(path)
        assert resp.status_code == 200

    def test_owner_email_match_is_case_insensitive(self, client, app, outbox):
        register_and_login(client, email="Owner@Example.com")
        resp = client.get("/admin/users")
        assert resp.status_code == 200

    def test_add_time_requires_owner_not_just_login(self, client, app, outbox):
        target = app.test_client()
        target_id = register_and_login(target, email="target2@example.com")

        register_and_login(client, email="not-the-owner2@example.com")
        resp = add_time(client, target_id)
        assert resp.status_code == 404

        # Refused attempt must not have touched anything.
        with app.app_context():
            db = get_db()
            row = db.execute("SELECT * FROM user_state WHERE user_id = %s", (target_id,)).fetchone()
        assert row is None

    def test_add_time_requires_csrf_header(self, client, app, outbox):
        target = app.test_client()
        target_id = register_and_login(target, email="target3@example.com")

        register_and_login(client, email="owner@example.com")
        resp = client.post(
            "/admin/users/{}/add-time".format(target_id),
            data={"minutes": "30", "date": current_week_start().isoformat(),
                  "course": "Math", "reason": "test"},
        )
        assert resp.status_code == 403

    def test_add_time_404s_for_unknown_user(self, client, app, outbox):
        register_and_login(client, email="owner@example.com")
        resp = add_time(client, 999999)
        assert resp.status_code == 404

    @pytest.mark.parametrize("field,value", [
        ("minutes", "0"),
        ("minutes", "not-a-number"),
        ("minutes", "99999"),
        ("course", ""),
        ("reason", ""),
    ])
    def test_add_time_rejects_bad_input(self, client, app, outbox, field, value):
        target = app.test_client()
        target_id = register_and_login(target, email="target4@example.com")

        register_and_login(client, email="owner@example.com")
        form = {"minutes": "30", "date": current_week_start().isoformat(),
                "course": "Math", "reason": "test"}
        form[field] = value
        resp = client.post(
            "/admin/users/{}/add-time".format(target_id), data=form, headers=JSON_HEADERS
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"

    def test_add_time_rejects_future_date(self, client, app, outbox):
        import datetime
        from server.db import utcnow

        target = app.test_client()
        target_id = register_and_login(target, email="target5@example.com")

        register_and_login(client, email="owner@example.com")
        future = (utcnow().date() + datetime.timedelta(days=2)).isoformat()
        resp = client.post(
            "/admin/users/{}/add-time".format(target_id),
            data={"minutes": "30", "date": future, "course": "Math", "reason": "test"},
            headers=JSON_HEADERS,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"

    # -----------------------------------------------------------------
    # The correction itself: session injection, versioning, leaderboard,
    # audit trail.
    # -----------------------------------------------------------------

    def test_add_time_injects_session_into_empty_state(self, client, app, outbox):
        target = app.test_client()
        target_id = register_and_login(target, email="target6@example.com")

        register_and_login(client, email="owner@example.com")
        date_str = current_week_start().isoformat()
        resp = add_time(client, target_id, minutes=45, date=date_str, course="Physics", reason="crashed")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        entry = body["session"]
        assert entry["durationSeconds"] == 45 * 60
        assert entry["date"] == date_str
        assert entry["course"] == "Physics"
        assert entry["type"] in ("Countdown", "Stopwatch")
        assert entry["addedByAdmin"] is True
        assert entry["adminReason"] == "crashed"
        assert isinstance(entry["hourOfDayExecuted"], int)
        assert isinstance(entry["timestamp"], str) and entry["timestamp"]

        state = get_state(target)
        assert state["version"] == 1
        assert state["payload"]["sessions"] == [entry]

    def test_add_time_preserves_existing_payload_and_unshifts(self, client, app, outbox):
        target = app.test_client()
        target_id = register_and_login(target, email="target7@example.com")

        existing_payload = {
            "profile": {"email": "target7@example.com", "dailyGoalHours": 3, "defaultTimerMinutes": 25},
            "courses": ["Math", "Physics"],
            "sessions": [{"date": "2020-01-01", "course": "Math", "type": "Countdown",
                          "durationSeconds": 600, "timestamp": "1:00 PM", "hourOfDayExecuted": 13}],
            "todoItems": [],
            "selectedCourse": "Math",
            "colourTheme": "blue",
            "streak": 2,
        }
        put_resp = put_state(target, existing_payload, 0)
        assert put_resp.status_code == 200

        register_and_login(client, email="owner@example.com")
        date_str = current_week_start().isoformat()
        resp = add_time(client, target_id, minutes=20, date=date_str, course="Chemistry", reason="fix")
        assert resp.status_code == 200

        state = get_state(target)
        assert state["version"] == 2  # bumped from 1 -> 2
        payload = state["payload"]
        assert payload["profile"] == existing_payload["profile"]
        assert payload["courses"] == existing_payload["courses"]
        assert payload["colourTheme"] == "blue"
        assert payload["streak"] == 2
        assert len(payload["sessions"]) == 2
        assert payload["sessions"][0]["course"] == "Chemistry"  # unshifted to the front
        assert payload["sessions"][0]["addedByAdmin"] is True
        assert payload["sessions"][1]["course"] == "Math"  # original session untouched

    def test_add_time_bumped_version_causes_stale_client_409(self, client, app, outbox):
        target = app.test_client()
        target_id = register_and_login(target, email="target8@example.com")
        put_state(target, {"sessions": []}, 0)  # version -> 1

        register_and_login(client, email="owner@example.com")
        add_time(client, target_id)  # version -> 2

        # The client still thinks it's at version 1 -- must hit the existing
        # 409-conflict path rather than silently clobbering the correction.
        stale_resp = put_state(target, {"sessions": []}, 1)
        assert stale_resp.status_code == 409
        assert stale_resp.get_json()["version"] == 2

    def test_add_time_updates_leaderboard_immediately(self, client, app, outbox):
        target = app.test_client()
        target_id = register_and_login(target, email="target9@example.com")

        register_and_login(client, email="owner@example.com")
        date_str = current_week_start().isoformat()
        add_time(client, target_id, minutes=30, date=date_str)

        lb = target.get("/api/leaderboard").get_json()
        assert lb["you"]["seconds"] == 30 * 60

    def test_add_time_out_of_week_date_does_not_move_this_week_total(self, client, app, outbox):
        import datetime

        target = app.test_client()
        target_id = register_and_login(target, email="target10@example.com")

        register_and_login(client, email="owner@example.com")
        last_week = (current_week_start() - datetime.timedelta(days=7)).isoformat()
        add_time(client, target_id, minutes=30, date=last_week)

        lb = target.get("/api/leaderboard").get_json()
        assert lb["you"]["seconds"] == 0  # correction landed outside the current week

        state = get_state(target)
        assert state["payload"]["sessions"][0]["date"] == last_week  # but is still in the log

    def test_add_time_writes_audit_row(self, client, app, outbox):
        target = app.test_client()
        target_id = register_and_login(target, email="target11@example.com")

        owner_id = register_and_login(client, email="owner@example.com")
        date_str = current_week_start().isoformat()
        add_time(client, target_id, minutes=15, date=date_str, course="Bio", reason="manual fix")

        with app.app_context():
            db = get_db()
            rows = db.execute("SELECT * FROM admin_actions").fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["admin_user_id"] == owner_id
        assert row["target_user_id"] == target_id
        assert row["action"] == "add_time"
        detail = json.loads(row["detail"])
        assert detail == {"minutes": 15, "date": date_str, "course": "Bio", "reason": "manual fix"}

    def test_admin_actions_page_shows_recent_actions(self, client, app, outbox):
        target = app.test_client()
        target_id = register_and_login(target, email="target12@example.com")

        register_and_login(client, email="owner@example.com")
        add_time(client, target_id, minutes=15, course="Bio", reason="manual fix reason marker")

        resp = client.get("/admin/actions")
        body = resp.get_data(as_text=True)
        assert "add_time" in body
        assert "manual fix reason marker" in body
        assert "target12@example.com" in body

    # -----------------------------------------------------------------
    # /admin/users must not leak sensitive fields
    # -----------------------------------------------------------------

    def test_admin_users_page_does_not_leak_sensitive_fields(self, client, app, outbox):
        target = app.test_client()
        register_and_login(target, email="target13@example.com", password="supersecretpw1")
        put_state(target, {"sessions": [{"date": "2020-01-01", "course": "Math", "type": "Countdown",
                                          "durationSeconds": 60, "timestamp": "1:00 PM",
                                          "hourOfDayExecuted": 13}]}, 0)

        register_and_login(client, email="owner@example.com")
        resp = client.get("/admin/users")
        body = resp.get_data(as_text=True)
        assert "target13@example.com" in body  # email IS shown -- needed to identify people
        assert "supersecretpw1" not in body
        assert "pbkdf2_sha256" not in body  # no password hash fragments
        assert "durationSeconds" not in body  # no raw payload/session JSON dumped
