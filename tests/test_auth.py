"""Tests for server/auth.py -- register/verify/login/logout/me, resend,
forgot/reset, change-password, CSRF and rate limiting. See spec section 10.
"""
import json
import re

JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def register(client, email="user@example.com", password="pw123456", display_name=None):
    body = {"email": email, "password": password}
    if display_name:
        body["display_name"] = display_name
    return client.post(
        "/api/auth/register",
        data=json.dumps(body),
        content_type="application/json",
        headers=JSON_HEADERS,
    )


def login(client, email, password):
    return client.post(
        "/api/auth/login",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )


def extract_token(text):
    match = re.search(r"token=([A-Za-z0-9_\-]+)", text)
    assert match, "no token found in email body: {}".format(text)
    return match.group(1)


def latest_link_token(outbox):
    assert outbox, "outbox is empty"
    return extract_token(outbox[-1]["text"])


def _verify_user(client, outbox, email="user@example.com", password="pw123456"):
    register(client, email=email, password=password)
    token = latest_link_token(outbox)
    client.get("/verify?token={}".format(token))


# --------------------------------------------------------------------- register

def test_register_returns_202_and_generic_message(client, outbox):
    resp = register(client)
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["ok"] is True
    assert "message" in data
    assert len(outbox) == 1
    assert outbox[0]["to"] == "user@example.com"


def test_register_existing_email_returns_same_generic_202(client, outbox):
    register(client)
    outbox.clear()
    resp = register(client)  # same email again
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["ok"] is True
    # Enumeration resistance: no new email for an address that's already registered.
    assert len(outbox) == 0


def test_register_rejects_invalid_email(client):
    resp = client.post(
        "/api/auth/register",
        data=json.dumps({"email": "not-an-email", "password": "pw123456"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_register_rejects_weak_password(client):
    resp = client.post(
        "/api/auth/register",
        data=json.dumps({"email": "weak@example.com", "password": "short1"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_register_rejects_password_without_digit(client):
    resp = client.post(
        "/api/auth/register",
        data=json.dumps({"email": "noletter@example.com", "password": "alllettersnodigits"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_register_requires_csrf_header(client):
    resp = client.post(
        "/api/auth/register",
        data=json.dumps({"email": "nocsrf@example.com", "password": "pw123456"}),
        content_type="application/json",
    )
    assert resp.status_code == 403


# ------------------------------------------------------- verification gating

def test_unverified_login_is_blocked(client, outbox):
    register(client)
    resp = login(client, "user@example.com", "pw123456")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "email_unverified"


def test_verification_token_unlocks_login(client, app, outbox):
    register(client)
    token = latest_link_token(outbox)

    verify_resp = client.get("/verify?token={}".format(token))
    assert verify_resp.status_code == 200

    login_resp = login(client, "user@example.com", "pw123456")
    assert login_resp.status_code == 200
    data = login_resp.get_json()
    assert data["ok"] is True
    assert data["user"]["email"] == "user@example.com"
    assert data["user"]["is_verified"] is True
    assert "aurastudy_session" in login_resp.headers.get("Set-Cookie", "")


def test_verification_token_is_single_use(client, app, outbox):
    register(client)
    token = latest_link_token(outbox)
    client.get("/verify?token={}".format(token))

    from server.db import get_db

    with app.app_context():
        db = get_db()
        row = db.execute("SELECT used_at FROM email_tokens WHERE purpose='verify'").fetchone()
        used_at_first = row["used_at"]
        assert used_at_first is not None

    # Reusing the same (now-used) token must not error, and must not disturb state.
    second_resp = client.get("/verify?token={}".format(token))
    assert second_resp.status_code == 200

    with app.app_context():
        db = get_db()
        row = db.execute("SELECT used_at FROM email_tokens WHERE purpose='verify'").fetchone()
        assert row["used_at"] == used_at_first


def test_verification_token_expires(client, app, outbox):
    register(client)
    token = latest_link_token(outbox)

    from server.db import get_db

    with app.app_context():
        db = get_db()
        db.execute(
            "UPDATE email_tokens SET expires_at = '2000-01-01T00:00:00+00:00' WHERE purpose='verify'"
        )
        db.commit()

    client.get("/verify?token={}".format(token))
    # Still unverified -> login must still be blocked.
    resp = login(client, "user@example.com", "pw123456")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "email_unverified"


def test_verify_unknown_token_does_not_verify_anyone(client, outbox):
    register(client)
    resp = client.get("/verify?token=totally-made-up-token")
    assert resp.status_code == 200
    login_resp = login(client, "user@example.com", "pw123456")
    assert login_resp.status_code == 403


def test_resend_verification_is_generic_and_works(client, outbox):
    register(client)
    outbox.clear()
    resp = client.post(
        "/api/auth/resend-verification",
        data=json.dumps({"email": "user@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 202
    assert len(outbox) == 1

    # Unknown email -> identical response shape, no email sent.
    outbox.clear()
    resp2 = client.post(
        "/api/auth/resend-verification",
        data=json.dumps({"email": "ghost@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp2.status_code == 202
    assert resp2.get_json()["ok"] is True
    assert len(outbox) == 0


# --------------------------------------------------- login failures / limits

def test_wrong_password_returns_401(client, outbox):
    _verify_user(client, outbox)
    resp = login(client, "user@example.com", "wrongpassword1")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_credentials"


def test_login_validation_error_on_missing_fields(client):
    resp = client.post(
        "/api/auth/login",
        data=json.dumps({"email": "user@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_eleven_bad_attempts_rate_limited(client, outbox):
    _verify_user(client, outbox)
    for _ in range(10):
        resp = login(client, "user@example.com", "wrongpassword1")
        assert resp.status_code == 401
    resp = login(client, "user@example.com", "wrongpassword1")
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"


def test_login_requires_csrf_header(client, outbox):
    _verify_user(client, outbox)
    resp = client.post(
        "/api/auth/login",
        data=json.dumps({"email": "user@example.com", "password": "pw123456"}),
        content_type="application/json",
    )
    assert resp.status_code == 403


# --------------------------------------------------------------- logout / me

def test_login_logout_me_flow(client, outbox):
    _verify_user(client, outbox)
    login_resp = login(client, "user@example.com", "pw123456")
    assert login_resp.status_code == 200

    me_resp = client.get("/api/auth/me", headers=JSON_HEADERS)
    assert me_resp.status_code == 200
    me_data = me_resp.get_json()
    assert me_data["user"]["email"] == "user@example.com"
    assert me_data["spotify_connected"] is False

    logout_resp = client.post("/api/auth/logout", headers=JSON_HEADERS)
    assert logout_resp.status_code == 200
    assert logout_resp.get_json()["ok"] is True

    me_after = client.get("/api/auth/me", headers=JSON_HEADERS)
    assert me_after.status_code == 401


def test_me_requires_login(client):
    resp = client.get("/api/auth/me", headers=JSON_HEADERS)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthenticated"


# ------------------------------------------------------- forgot / reset flow

def test_forgot_password_flow(client, outbox):
    _verify_user(client, outbox)
    outbox.clear()

    resp = client.post(
        "/api/auth/forgot-password",
        data=json.dumps({"email": "user@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 202
    assert len(outbox) == 1

    reset_token = extract_token(outbox[-1]["text"])

    reset_resp = client.post(
        "/api/auth/reset-password",
        data=json.dumps({"token": reset_token, "password": "newpassword1"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert reset_resp.status_code == 200
    assert reset_resp.get_json()["ok"] is True

    old_login = login(client, "user@example.com", "pw123456")
    assert old_login.status_code == 401

    new_login = login(client, "user@example.com", "newpassword1")
    assert new_login.status_code == 200


def test_forgot_password_unknown_email_is_generic(client, outbox):
    resp = client.post(
        "/api/auth/forgot-password",
        data=json.dumps({"email": "ghost@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 202
    assert resp.get_json()["ok"] is True
    assert len(outbox) == 0


def test_reset_password_rejects_used_token(client, outbox):
    _verify_user(client, outbox)
    outbox.clear()
    client.post(
        "/api/auth/forgot-password",
        data=json.dumps({"email": "user@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    reset_token = extract_token(outbox[-1]["text"])

    first = client.post(
        "/api/auth/reset-password",
        data=json.dumps({"token": reset_token, "password": "newpassword1"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert first.status_code == 200

    second = client.post(
        "/api/auth/reset-password",
        data=json.dumps({"token": reset_token, "password": "anotherpassword2"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert second.status_code == 400
    assert second.get_json()["error"] == "invalid_token"


def test_reset_password_rejects_expired_token(client, app, outbox):
    _verify_user(client, outbox)
    outbox.clear()
    client.post(
        "/api/auth/forgot-password",
        data=json.dumps({"email": "user@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    reset_token = extract_token(outbox[-1]["text"])

    from server.db import get_db

    with app.app_context():
        db = get_db()
        db.execute(
            "UPDATE email_tokens SET expires_at = '2000-01-01T00:00:00+00:00' WHERE purpose='reset'"
        )
        db.commit()

    resp = client.post(
        "/api/auth/reset-password",
        data=json.dumps({"token": reset_token, "password": "newpassword1"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_token"


def test_reset_password_rejects_weak_password(client, outbox):
    _verify_user(client, outbox)
    outbox.clear()
    client.post(
        "/api/auth/forgot-password",
        data=json.dumps({"email": "user@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    reset_token = extract_token(outbox[-1]["text"])

    resp = client.post(
        "/api/auth/reset-password",
        data=json.dumps({"token": reset_token, "password": "short"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


# ---------------------------------------------------------- change password

def test_change_password_flow(client, outbox):
    _verify_user(client, outbox)
    login(client, "user@example.com", "pw123456")

    resp = client.post(
        "/api/auth/change-password",
        data=json.dumps({"current_password": "pw123456", "new_password": "brandnew1"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    old_login = login(client, "user@example.com", "pw123456")
    assert old_login.status_code == 401
    new_login = login(client, "user@example.com", "brandnew1")
    assert new_login.status_code == 200


def test_change_password_wrong_current_returns_401(client, outbox):
    _verify_user(client, outbox)
    login(client, "user@example.com", "pw123456")

    resp = client.post(
        "/api/auth/change-password",
        data=json.dumps({"current_password": "wrongpw1", "new_password": "brandnew1"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 401


def test_change_password_requires_login(client):
    resp = client.post(
        "/api/auth/change-password",
        data=json.dumps({"current_password": "a", "new_password": "brandnew1"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 401
