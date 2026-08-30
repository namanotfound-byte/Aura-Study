"""Tests for server/spotify_requests.py: the /api/spotify/access-request
CRUD endpoints, and the owner-only /admin/spotify-requests page + its
POST .../<id>/mark-added action (both registered in server/app.py).

Uses the `client`/`app`/`outbox` fixtures from tests/conftest.py. Runs
against both database backends (see conftest's `backend` fixture).
"""
import json

import pytest

from server import spotify_requests
from server.db import get_db

JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def register_and_login(client, email="requester@example.com", password="hunter2pw"):
    client.post(
        "/api/auth/register",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    # server/auth.py's register() always lowercases the email before storing
    # it, so look it back up lowercased too -- SQLite's `users.email` column
    # is COLLATE NOCASE (case-insensitive comparison masks a mismatch here),
    # but Postgres's is plain TEXT (only the *index* is on lower(email)), so
    # a mixed-case `email` argument (deliberately used by this file's
    # case-insensitivity tests) would silently match zero rows there.
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


def submit(client, spotify_email="listener@example.com"):
    return client.post(
        "/api/spotify/access-request",
        data=json.dumps({"spotify_email": spotify_email}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )


# ---------------------------------------------------------------------------
# GET /api/spotify/access-request
# ---------------------------------------------------------------------------

def test_get_when_none_submitted(client, outbox):
    register_and_login(client)
    resp = client.get("/api/spotify/access-request")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "submitted": False, "spotify_email": None, "status": None, "submitted_at": None,
    }


def test_get_requires_login(client):
    resp = client.get("/api/spotify/access-request")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthenticated"


# ---------------------------------------------------------------------------
# POST /api/spotify/access-request
# ---------------------------------------------------------------------------

def test_post_creates_pending_request(client, outbox):
    register_and_login(client)
    resp = submit(client, "listener@example.com")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "status": "pending"}

    body = client.get("/api/spotify/access-request").get_json()
    assert body["submitted"] is True
    assert body["spotify_email"] == "listener@example.com"
    assert body["status"] == "pending"
    assert body["submitted_at"] is not None


def test_post_second_time_updates_not_duplicates(client, app, outbox):
    user_id = register_and_login(client)
    submit(client, "first@example.com")
    submit(client, "second@example.com")

    with app.app_context():
        db = get_db()
        rows = db.execute(
            "SELECT * FROM spotify_access_requests WHERE user_id = %s", (user_id,)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["spotify_email"] == "second@example.com"

    assert client.get("/api/spotify/access-request").get_json()["spotify_email"] == "second@example.com"


def test_post_after_added_resets_status_to_pending(client, app, outbox):
    user_id = register_and_login(client)
    submit(client, "listener@example.com")
    with app.app_context():
        db = get_db()
        db.execute("UPDATE spotify_access_requests SET status = 'added' WHERE user_id = %s", (user_id,))
        db.commit()

    resp = submit(client, "listener@example.com")
    assert resp.get_json() == {"ok": True, "status": "pending"}
    assert client.get("/api/spotify/access-request").get_json()["status"] == "pending"


def test_post_invalid_email_rejected(client, outbox):
    register_and_login(client)
    resp = submit(client, "not-an-email")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_post_missing_email_rejected(client, outbox):
    register_and_login(client)
    resp = client.post(
        "/api/spotify/access-request",
        data=json.dumps({}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_post_requires_csrf_header(client, outbox):
    register_and_login(client)
    resp = client.post(
        "/api/spotify/access-request",
        data=json.dumps({"spotify_email": "listener@example.com"}),
        content_type="application/json",
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "csrf_failed"


def test_post_requires_login(client):
    resp = client.post(
        "/api/spotify/access-request",
        data=json.dumps({"spotify_email": "listener@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 401


def test_post_is_rate_limited(client, outbox):
    register_and_login(client)
    last = None
    for _ in range(spotify_requests.SUBMIT_RATE_LIMIT + 1):
        last = submit(client, "listener@example.com")
    assert last.status_code == 429
    assert last.get_json()["error"] == "rate_limited"


# ---------------------------------------------------------------------------
# DELETE /api/spotify/access-request
# ---------------------------------------------------------------------------

def test_delete_withdraws_request(client, app, outbox):
    user_id = register_and_login(client)
    submit(client, "listener@example.com")

    resp = client.delete("/api/spotify/access-request", headers=JSON_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}

    assert client.get("/api/spotify/access-request").get_json()["submitted"] is False
    with app.app_context():
        db = get_db()
        rows = db.execute(
            "SELECT * FROM spotify_access_requests WHERE user_id = %s", (user_id,)
        ).fetchall()
    assert rows == []


def test_delete_when_nothing_submitted_is_still_ok(client, outbox):
    register_and_login(client)
    resp = client.delete("/api/spotify/access-request", headers=JSON_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


def test_delete_requires_csrf_header(client, outbox):
    register_and_login(client)
    resp = client.delete("/api/spotify/access-request")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "csrf_failed"


def test_delete_requires_login(client):
    resp = client.delete("/api/spotify/access-request", headers=JSON_HEADERS)
    assert resp.status_code == 401


def test_request_is_private_to_the_submitting_user(client, app, outbox):
    """A second user's GET must never see the first user's spotify_email."""
    register_and_login(client, email="user-a@example.com")
    submit(client, "user-a-secret@example.com")

    user_b = app.test_client()
    register_and_login(user_b, email="user-b@example.com")
    body = user_b.get("/api/spotify/access-request").get_json()
    assert body == {"submitted": False, "spotify_email": None, "status": None, "submitted_at": None}


# ---------------------------------------------------------------------------
# Owner-only admin page: GET /admin/spotify-requests,
# POST /admin/spotify-requests/<id>/mark-added
#
# The `owner_email` fixture here is conftest's default ("") unless a test
# (or the TestAdminGate class below) overrides it -- and it MUST be set
# before the `app` fixture builds the app, since Config is read once inside
# create_app(). See conftest.py's `owner_email`/`app` fixtures for why.
# ---------------------------------------------------------------------------

def test_admin_page_anonymous_visitor_is_redirected(client):
    resp = client.get("/admin/spotify-requests")
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/login")


def test_admin_page_unreachable_when_owner_email_unset(client, app, outbox):
    # Register a user and log in -- there simply is no configured owner, so
    # even this authenticated user must be refused with a plain 404.
    register_and_login(client, email="someone@example.com")
    resp = client.get("/admin/spotify-requests")
    assert resp.status_code == 404


def test_admin_mark_added_unreachable_when_owner_email_unset(client, app, outbox):
    register_and_login(client, email="someone@example.com")
    resp = client.post("/admin/spotify-requests/1/mark-added", headers=JSON_HEADERS)
    assert resp.status_code == 404


class TestAdminGate:
    """Owner-gated admin page tests, run with OWNER_EMAIL actually set."""

    @pytest.fixture
    def owner_email(self):
        return "owner@example.com"

    def test_non_owner_gets_404_not_403(self, client, app, outbox):
        register_and_login(client, email="not-the-owner@example.com")
        resp = client.get("/admin/spotify-requests")
        assert resp.status_code == 404

    def test_non_owner_response_never_contains_a_user_email(self, client, app, outbox):
        submitter = app.test_client()
        register_and_login(submitter, email="submitter@example.com")
        submit(submitter, "secret-listener@example.com")

        register_and_login(client, email="not-the-owner@example.com")
        resp = client.get("/admin/spotify-requests")
        assert resp.status_code == 404
        body = resp.get_data(as_text=True)
        assert "secret-listener@example.com" not in body
        assert "submitter@example.com" not in body

    def test_owner_sees_the_list(self, client, app, outbox):
        submitter = app.test_client()
        register_and_login(submitter, email="submitter@example.com")
        submit(submitter, "secret-listener@example.com")

        register_and_login(client, email="owner@example.com")
        resp = client.get("/admin/spotify-requests")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "secret-listener@example.com" in body
        assert "submitter@example.com" in body
        assert "pending" in body

    def test_owner_email_match_is_case_insensitive(self, client, app, outbox):
        register_and_login(client, email="Owner@Example.com")
        resp = client.get("/admin/spotify-requests")
        assert resp.status_code == 200

    def test_mark_added_requires_owner_not_just_login(self, client, app, outbox):
        submitter = app.test_client()
        submitter_id = register_and_login(submitter, email="submitter2@example.com")
        submit(submitter, "listener2@example.com")
        with app.app_context():
            db = get_db()
            req_id = db.execute(
                "SELECT id FROM spotify_access_requests WHERE user_id = %s", (submitter_id,)
            ).fetchone()["id"]

        register_and_login(client, email="not-the-owner2@example.com")
        resp = client.post(
            "/admin/spotify-requests/{}/mark-added".format(req_id), headers=JSON_HEADERS
        )
        assert resp.status_code == 404

        # And the row must be untouched by the refused attempt.
        with app.app_context():
            db = get_db()
            status = db.execute(
                "SELECT status FROM spotify_access_requests WHERE id = %s", (req_id,)
            ).fetchone()["status"]
        assert status == "pending"

    def test_owner_can_mark_added(self, client, app, outbox):
        submitter = app.test_client()
        submitter_id = register_and_login(submitter, email="submitter3@example.com")
        submit(submitter, "listener3@example.com")
        with app.app_context():
            db = get_db()
            req_id = db.execute(
                "SELECT id FROM spotify_access_requests WHERE user_id = %s", (submitter_id,)
            ).fetchone()["id"]

        register_and_login(client, email="owner@example.com")
        resp = client.post(
            "/admin/spotify-requests/{}/mark-added".format(req_id), headers=JSON_HEADERS
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}

        assert submitter.get("/api/spotify/access-request").get_json()["status"] == "added"

        # The now-added row must drop off the admin page's "pending" listing
        # affordance (no more Mark added control for it), while still being
        # visible to the owner in general.
        list_resp = client.get("/admin/spotify-requests")
        body = list_resp.get_data(as_text=True)
        assert "listener3@example.com" in body

    def test_mark_added_requires_csrf_header(self, client, app, outbox):
        submitter = app.test_client()
        submitter_id = register_and_login(submitter, email="submitter4@example.com")
        submit(submitter, "listener4@example.com")
        with app.app_context():
            db = get_db()
            req_id = db.execute(
                "SELECT id FROM spotify_access_requests WHERE user_id = %s", (submitter_id,)
            ).fetchone()["id"]

        register_and_login(client, email="owner@example.com")
        resp = client.post("/admin/spotify-requests/{}/mark-added".format(req_id))
        assert resp.status_code == 403

    def test_mark_added_anonymous_is_redirected(self, client):
        resp = client.post("/admin/spotify-requests/1/mark-added", headers=JSON_HEADERS)
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/login")
