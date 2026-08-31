"""Tests for server/support.py: the /api/support/* user-facing endpoints,
and the owner-only /admin/support (+ its reply action) routes registered in
server/app.py.

Uses the `client`/`app`/`outbox` fixtures from tests/conftest.py. Runs
against both database backends (see conftest's `backend` fixture).
"""
import json

import pytest

from server import support
from server.db import get_db, utcnow_iso

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


def send(client, body="I have a doubt about the timer.", headers=JSON_HEADERS):
    return client.post(
        "/api/support/messages",
        data=json.dumps({"body": body}),
        content_type="application/json",
        headers=headers,
    )


def admin_reply(client, user_id, body="Here's the answer.", headers=JSON_HEADERS):
    return client.post(
        "/admin/support/{}/reply".format(user_id),
        data=json.dumps({"body": body}),
        content_type="application/json",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Validation (pure function)
# ---------------------------------------------------------------------------

def test_validate_body_rejects_empty_and_oversized():
    assert support.validate_body("")[0] is None
    assert support.validate_body("   ")[0] is None
    assert support.validate_body("x" * (support.MAX_BODY_LENGTH + 1))[0] is None
    assert support.validate_body(None)[0] is None
    assert support.validate_body("  hello  ") == ("hello", None)


def test_validate_body_accepts_max_length():
    text = "x" * support.MAX_BODY_LENGTH
    assert support.validate_body(text) == (text, None)


# ---------------------------------------------------------------------------
# GET /api/support/messages
# ---------------------------------------------------------------------------

def test_get_messages_requires_login(client):
    resp = client.get("/api/support/messages")
    assert resp.status_code == 401


def test_get_messages_empty_by_default(client, outbox):
    register_and_login(client)
    resp = client.get("/api/support/messages")
    assert resp.status_code == 200
    assert resp.get_json() == {"messages": [], "unread_replies": 0}


# ---------------------------------------------------------------------------
# POST /api/support/messages
# ---------------------------------------------------------------------------

def test_post_message_requires_login(client):
    resp = send(client)
    assert resp.status_code == 401


def test_post_message_requires_csrf_header(client, outbox):
    register_and_login(client)
    resp = client.post(
        "/api/support/messages",
        data=json.dumps({"body": "hi"}),
        content_type="application/json",
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "csrf_failed"


def test_post_message_creates_and_lists(client, outbox):
    register_and_login(client)
    resp = send(client, "How do I reset my streak?")
    assert resp.status_code == 201
    assert resp.get_json() == {"ok": True}

    body = client.get("/api/support/messages").get_json()
    assert len(body["messages"]) == 1
    msg = body["messages"][0]
    assert msg["body"] == "How do I reset my streak?"
    assert msg["from_admin"] is False
    assert msg["created_at"] is not None
    assert body["unread_replies"] == 0


@pytest.mark.parametrize("raw", ["", "   ", "x" * 2001])
def test_post_message_rejects_invalid_body(client, outbox, raw):
    register_and_login(client)
    resp = send(client, raw)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_post_message_missing_body_field_rejected(client, outbox):
    register_and_login(client)
    resp = client.post(
        "/api/support/messages",
        data=json.dumps({}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_post_message_is_rate_limited(client, outbox):
    register_and_login(client)
    last = None
    for _ in range(support.SEND_RATE_LIMIT + 1):
        last = send(client, "spam")
    assert last.status_code == 429
    assert last.get_json()["error"] == "rate_limited"


# ---------------------------------------------------------------------------
# POST /api/support/read
# ---------------------------------------------------------------------------

def test_read_requires_login(client):
    resp = client.post("/api/support/read", headers=JSON_HEADERS)
    assert resp.status_code == 401


def test_read_requires_csrf_header(client, outbox):
    register_and_login(client)
    resp = client.post("/api/support/read")
    assert resp.status_code == 403


def test_read_with_nothing_unread_is_still_ok(client, outbox):
    register_and_login(client)
    resp = client.post("/api/support/read", headers=JSON_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


# ---------------------------------------------------------------------------
# End-to-end: user asks, owner replies, user sees it and unread clears.
# ---------------------------------------------------------------------------

class TestConversationFlow:
    @pytest.fixture
    def owner_email(self):
        return "owner@example.com"

    def test_full_conversation_round_trip(self, client, app, outbox):
        asker = app.test_client()
        asker_id = register_and_login(asker, email="asker@example.com")
        send(asker, "Why is my streak reset?")

        register_and_login(client, email="owner@example.com")
        support_page = client.get("/admin/support").get_data(as_text=True)
        assert "asker@example.com" in support_page
        assert "Why is my streak reset?" in support_page
        assert "Unanswered" in support_page

        reply_resp = admin_reply(client, asker_id, "Streaks reset after a missed day.")
        assert reply_resp.status_code == 200
        assert reply_resp.get_json() == {"ok": True}

        # User sees the reply and an unread count.
        body = asker.get("/api/support/messages").get_json()
        assert len(body["messages"]) == 2
        assert body["messages"][0]["from_admin"] is False
        assert body["messages"][1]["from_admin"] is True
        assert body["messages"][1]["body"] == "Streaks reset after a missed day."
        assert body["unread_replies"] == 1

        # Marking read clears the count.
        mark_resp = asker.post("/api/support/read", headers=JSON_HEADERS)
        assert mark_resp.status_code == 200
        body_after = asker.get("/api/support/messages").get_json()
        assert body_after["unread_replies"] == 0

        # And the conversation no longer shows as unanswered on the admin page.
        support_page_after = client.get("/admin/support?user_id={}".format(asker_id)).get_data(as_text=True)
        assert "Streaks reset after a missed day." in support_page_after

    def test_admin_reply_requires_owner_not_just_login(self, client, app, outbox):
        asker = app.test_client()
        asker_id = register_and_login(asker, email="asker2@example.com")
        send(asker, "help")

        register_and_login(client, email="not-the-owner@example.com")
        resp = admin_reply(client, asker_id, "nope")
        assert resp.status_code == 404

        # Refused reply must not have been written.
        thread = asker.get("/api/support/messages").get_json()["messages"]
        assert len(thread) == 1

    def test_admin_reply_requires_csrf_header(self, client, app, outbox):
        asker = app.test_client()
        asker_id = register_and_login(asker, email="asker3@example.com")
        send(asker, "help")

        register_and_login(client, email="owner@example.com")
        resp = client.post(
            "/admin/support/{}/reply".format(asker_id),
            data=json.dumps({"body": "answer"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_admin_reply_404s_for_unknown_user(self, client, app, outbox):
        register_and_login(client, email="owner@example.com")
        resp = admin_reply(client, 999999, "hi")
        assert resp.status_code == 404

    def test_admin_reply_validates_body(self, client, app, outbox):
        asker = app.test_client()
        asker_id = register_and_login(asker, email="asker4@example.com")
        send(asker, "help")

        register_and_login(client, email="owner@example.com")
        resp = admin_reply(client, asker_id, "")
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"

    def test_admin_support_page_unreachable_when_owner_unset(self, client, outbox):
        register_and_login(client, email="someone@example.com")
        resp = client.get("/admin/support")
        assert resp.status_code == 404

    def test_admin_support_anonymous_redirected(self, client):
        resp = client.get("/admin/support")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/login")


# ---------------------------------------------------------------------------
# Cross-user isolation -- a user must only ever see their own conversation,
# by any means: their own GET, and another ordinary (non-owner) user's GET.
# ---------------------------------------------------------------------------

def test_user_cannot_read_another_users_messages_via_get(client, app, outbox):
    user_a = app.test_client()
    register_and_login(user_a, email="iso-a@example.com")
    send(user_a, "user A's private question")

    user_b = client
    register_and_login(user_b, email="iso-b@example.com")
    body = user_b.get("/api/support/messages").get_json()
    assert body["messages"] == []
    assert "user A's private question" not in json.dumps(body)


def test_user_cannot_mark_another_users_messages_read(client, app, outbox):
    """POST /api/support/read only ever touches the caller's own rows --
    verified directly against the DB rather than trusting the response,
    since a bug here could look identical to "already read" from the API."""
    user_a = app.test_client()
    user_a_id = register_and_login(user_a, email="iso-c@example.com")
    send(user_a, "another private question")

    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO support_messages (user_id, body, from_admin, created_at) "
            "VALUES (%s, %s, %s, %s)",
            (user_a_id, "an admin reply", True, utcnow_iso()),
        )
        db.commit()

    user_b = client
    register_and_login(user_b, email="iso-d@example.com")
    user_b.post("/api/support/read", headers=JSON_HEADERS)

    with app.app_context():
        db = get_db()
        row = db.execute(
            "SELECT read_at FROM support_messages WHERE user_id = %s AND from_admin = %s",
            (user_a_id, True),
        ).fetchone()
    assert row["read_at"] is None  # user B's read call must not touch user A's row


def test_user_b_response_never_contains_user_a_content(client, app, outbox):
    user_a = app.test_client()
    register_and_login(user_a, email="iso-e@example.com")
    send(user_a, "SECRET-QUESTION-CONTENT-XYZ")

    user_b = client
    register_and_login(user_b, email="iso-f@example.com")
    send(user_b, "user b's own question")

    resp = user_b.get("/api/support/messages")
    raw = resp.get_data(as_text=True)
    assert "SECRET-QUESTION-CONTENT-XYZ" not in raw
    assert "iso-e@example.com" not in raw
