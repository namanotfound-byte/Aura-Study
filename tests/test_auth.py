"""Tests for server/auth.py -- register/verify/login/logout/me, resend,
forgot/reset, change-password, CSRF and rate limiting. See spec section 10.
"""
import datetime
import json
import re

from server.security import is_password_breached as _real_is_password_breached

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


def _defuse_backoff(app, rate_key):
    """Push every recorded auth_attempts row for `rate_key` back in time far
    enough to fall outside the login exponential-backoff's short lockout
    window, while staying well inside the flat rate limiter's 15-minute
    window. Lets a test drive the flat limiter to its exact boundary without
    tripping the (separately, directly tested) backoff, and without a real
    sleep."""
    with app.app_context():
        from server.db import get_db
        from server.security import LOCKOUT_WINDOW_MINUTES

        db = get_db()
        aged = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=LOCKOUT_WINDOW_MINUTES + 1)
        ).isoformat()
        db.execute("UPDATE auth_attempts SET created_at = %s WHERE key = %s", (aged, rate_key))
        db.commit()


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


def test_register_rejects_breached_password(client, monkeypatch):
    """The `_stub_hibp` autouse fixture (conftest.py) makes every password
    "not breached" by default; override it here to prove a breached password
    is actually rejected, without ever touching the real HIBP API.

    Patches `server.auth.is_password_breached` specifically: auth.py does
    `from .security import is_password_breached`, which binds its own name
    at import time, so patching security.py's copy of the name (as the
    autouse fixture also does) does not affect what the route itself calls.
    """
    import server.auth as auth_module

    monkeypatch.setattr(auth_module, "is_password_breached", lambda pw: True)
    resp = client.post(
        "/api/auth/register",
        data=json.dumps({"email": "pwned@example.com", "password": "pw123456"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "password_breached"


def test_breached_password_registrations_still_count_against_rate_limit(client, monkeypatch):
    """Regression test for a gap found in security review: record_attempt()
    for the per-IP register limiter used to run AFTER the is_password_breached()
    check, so a request rejected for a breached password never counted
    against the limit. Since HIBP is a real outbound network call, that let
    an unauthenticated caller force unlimited HIBP requests (and tie up a
    worker thread on each) by always sending a common breached password,
    completely bypassing the "5 registrations per IP per hour" limit. Each
    of these 5 requests is rejected as breached, but must still count -- the
    6th must be rate-limited, not another 400 password_breached."""
    import server.auth as auth_module

    monkeypatch.setattr(auth_module, "is_password_breached", lambda pw: True)
    for i in range(5):
        resp = client.post(
            "/api/auth/register",
            data=json.dumps({"email": "pwned{}@example.com".format(i), "password": "pw123456"}),
            content_type="application/json",
            headers=JSON_HEADERS,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "password_breached"

    resp = client.post(
        "/api/auth/register",
        data=json.dumps({"email": "pwned-sixth@example.com", "password": "pw123456"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"


def test_hibp_check_fails_open_when_unreachable(monkeypatch):
    """Unit-level test of the real is_password_breached implementation
    (captured as `_real_is_password_breached` above, before the autouse
    stub replaces `server.security.is_password_breached` for the duration
    of each test): a network failure talking to HIBP must be swallowed and
    treated as "not breached", never raised, and the real network must never
    be touched -- `requests.get` is mocked to raise."""
    import requests

    import server.security as security_module

    def _boom(*args, **kwargs):
        raise requests.exceptions.ConnectTimeout("simulated HIBP outage")

    monkeypatch.setattr(security_module.requests, "get", _boom)
    assert _real_is_password_breached("whatever-password-1") is False


def test_hibp_check_detects_breach_from_mocked_response(monkeypatch):
    """Unit-level test proving a genuine match in the (mocked) HIBP response
    is detected, and that only the 5-char SHA-1 prefix -- never the password
    or full hash -- is sent."""
    import hashlib

    import server.security as security_module

    password = "correcthorsebatterystaple"
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]

    class _FakeResponse:
        text = "{}:{}\nDEADBEEF00000000000000000000000000:1".format(suffix, 42)

        def raise_for_status(self):
            pass

    captured = {}

    def _fake_get(url, timeout=None, headers=None):
        captured["url"] = url
        captured["prefix_sent"] = prefix in url
        captured["full_hash_sent"] = sha1 in url
        return _FakeResponse()

    monkeypatch.setattr(security_module.requests, "get", _fake_get)
    assert _real_is_password_breached(password) is True
    assert captured["prefix_sent"] is True
    assert captured["full_hash_sent"] is False


def test_concurrent_duplicate_registration_returns_generic_202_not_500(client, outbox, monkeypatch):
    """Two registrations racing for the same email: the second one's
    duplicate-email pre-check can lose the race and see nothing, so it falls
    through to the INSERT, which then hits the database's own
    `lower(email)` uniqueness constraint. That must be caught and turned
    into the same generic 202 as the normal "already registered" path, never
    an unhandled 500 -- and, being identical either way, this also can't be
    used to detect the race (email enumeration).

    Simulated deterministically (rather than relying on real thread timing)
    by monkeypatching the pre-check helper to report "not found" even though
    the row already exists, exactly reproducing the race window.
    """
    import server.auth as auth_module

    resp1 = register(client, email="race@example.com", password="pw123456")
    assert resp1.status_code == 202
    outbox.clear()

    monkeypatch.setattr(auth_module, "_user_exists", lambda db, email: False)

    resp2 = register(client, email="race@example.com", password="pw123456")
    assert resp2.status_code == 202
    assert resp2.get_json()["message"] == auth_module.REGISTER_MESSAGE
    assert len(outbox) == 0  # no duplicate verification email sent

    # Exactly one user row exists -- the losing INSERT was rolled back, not
    # left half-applied.
    with client.application.app_context():
        from server.db import get_db

        count = get_db().execute(
            "SELECT COUNT(*) AS c FROM users WHERE email = %s", ("race@example.com",)
        ).fetchone()["c"]
    assert count == 1


# ------------------------------------------------------- verification gating

def test_unverified_login_is_blocked(client, outbox):
    register(client)
    resp = login(client, "user@example.com", "pw123456")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "email_unverified"


def test_verification_token_unlocks_login(client, app, outbox):
    register(client)
    token = latest_link_token(outbox)

    # /verify now auto-logs in on a valid token (redirect, not a 200 message
    # page) -- see the dedicated test_verify_auto_login_* tests below for the
    # full contract. This test's original point, that verifying unlocks
    # login for the account, still holds: an explicit login() afterwards
    # must also succeed, independent of the session /verify just created.
    verify_resp = client.get("/verify?token={}".format(token))
    assert verify_resp.status_code == 302
    assert verify_resp.headers["Location"] == "/app?verified=1"

    login_resp = login(client, "user@example.com", "pw123456")
    assert login_resp.status_code == 200
    data = login_resp.get_json()
    assert data["ok"] is True
    assert data["user"]["email"] == "user@example.com"
    assert data["user"]["is_verified"] is True
    assert "aurastudy_session" in login_resp.headers.get("Set-Cookie", "")


# ------------------------------------------------------- verify auto-login

def test_verify_auto_login_sets_session_and_redirects(client, app, outbox):
    """The success path (spec section 4): a valid, unused, unexpired token
    marks the account verified, issues a session exactly as login() does,
    and redirects to /app?verified=1 -- all in the one GET /verify request,
    no separate login step required."""
    register(client)
    token = latest_link_token(outbox)

    resp = client.get("/verify?token={}".format(token))
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/app?verified=1"

    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "aurastudy_session" in set_cookie
    # Same cookie flags login()'s set_session_cookie() uses.
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert "Path=/" in set_cookie

    # The session actually works -- no separate login() call needed.
    me_resp = client.get("/api/auth/me", headers=JSON_HEADERS)
    assert me_resp.status_code == 200
    me_data = me_resp.get_json()
    assert me_data["user"]["email"] == "user@example.com"
    assert me_data["user"]["is_verified"] is True

    with app.app_context():
        from server.db import get_db

        db = get_db()
        row = db.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()
        assert row["c"] == 1  # exactly one session, created by /verify itself


def test_verify_expired_token_does_not_create_session(client, app, outbox):
    register(client)
    token = latest_link_token(outbox)

    from server.db import get_db

    with app.app_context():
        db = get_db()
        db.execute(
            "UPDATE email_tokens SET expires_at = '2000-01-01T00:00:00+00:00' WHERE purpose='verify'"
        )
        db.commit()

    resp = client.get("/verify?token={}".format(token))
    assert resp.status_code == 200  # message page, not a redirect
    assert "aurastudy_session" not in resp.headers.get("Set-Cookie", "")

    me_resp = client.get("/api/auth/me", headers=JSON_HEADERS)
    assert me_resp.status_code == 401


def test_verify_already_used_token_does_not_create_session(client, app, outbox):
    register(client)
    token = latest_link_token(outbox)

    first = client.get("/verify?token={}".format(token))
    assert first.status_code == 302  # the first, valid use auto-logs in

    # Log back out so the client has no session, then reuse the same
    # (now-used) token.
    client.post("/api/auth/logout", headers=JSON_HEADERS)

    second = client.get("/verify?token={}".format(token))
    assert second.status_code == 200  # message page, not a redirect
    assert "aurastudy_session" not in second.headers.get("Set-Cookie", "")

    me_resp = client.get("/api/auth/me", headers=JSON_HEADERS)
    assert me_resp.status_code == 401

    from server.db import get_db

    with app.app_context():
        db = get_db()
        row = db.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()
        assert row["c"] == 0  # the one session from the first use was logged out, no second was made


def test_verify_unknown_token_does_not_create_session(client, outbox):
    register(client)
    resp = client.get("/verify?token=totally-made-up-token")
    assert resp.status_code == 200
    assert "aurastudy_session" not in resp.headers.get("Set-Cookie", "")

    me_resp = client.get("/api/auth/me", headers=JSON_HEADERS)
    assert me_resp.status_code == 401


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


def test_resend_verification_is_rate_limited_per_email(client, outbox):
    """Regression test: resend-verification previously had NO rate limit at
    all. Being unauthenticated and triggering a real SMTP send, that let
    anyone script an unbounded loop of requests against one victim's address
    -- either to spam their inbox or, at scale, to burn through the whole
    app's daily email-provider quota (Brevo's free tier is 300/day) so no
    verification or reset email gets through for *any* user. 3 requests for
    the same email should succeed (each actually emails); the 4th within the
    hour must be rejected before ever calling the mailer."""
    register(client)
    outbox.clear()
    for _ in range(3):
        resp = client.post(
            "/api/auth/resend-verification",
            data=json.dumps({"email": "user@example.com"}),
            content_type="application/json",
            headers=JSON_HEADERS,
        )
        assert resp.status_code == 202
    assert len(outbox) == 3

    resp = client.post(
        "/api/auth/resend-verification",
        data=json.dumps({"email": "user@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"
    assert len(outbox) == 3  # the 4th request never reached the mailer


# --------------------------------------------------- login failures / limits

def test_wrong_password_returns_401(client, outbox):
    _verify_user(client, outbox)
    resp = login(client, "user@example.com", "wrongpassword1")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_credentials"


def test_login_pays_pbkdf2_cost_for_nonexistent_email(client, monkeypatch):
    """Regression test for a timing side-channel found in security review:
    login() used to call verify_password() (a real PBKDF2-240k check) only
    when a user row was found, so a nonexistent email returned in roughly
    the time of one indexed SELECT while a wrong password for a real account
    paid the full hashing cost -- a measurable difference an attacker could
    use to enumerate registered emails via response *timing*, even though
    the response *body* ("invalid_credentials") is identical either way.

    Rather than asserting on wall-clock time (flaky in CI), this asserts on
    the actual mechanism of the fix: verify_password() must now be called
    exactly once whether or not the email exists, so both branches do the
    same PBKDF2 work."""
    import server.auth as auth_module

    calls = []
    real_verify = auth_module.verify_password

    def counting_verify(pw, stored):
        calls.append(stored)
        return real_verify(pw, stored)

    monkeypatch.setattr(auth_module, "verify_password", counting_verify)

    resp = login(client, "nobody-like-this-exists@example.com", "whatever123")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_credentials"
    assert len(calls) == 1  # verify_password ran even though no user matched


def test_login_validation_error_on_missing_fields(client):
    resp = client.post(
        "/api/auth/login",
        data=json.dumps({"email": "user@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_eleven_bad_attempts_rate_limited(client, outbox, app):
    _verify_user(client, outbox)
    # The exponential backoff added on top of this flat limit (tested
    # separately below) would otherwise start blocking these with
    # "account_locked" well before the 10th attempt -- age each failure out
    # of the backoff's short window immediately so this exercises the flat
    # 10-per-15-minutes limiter specifically, same as before backoff existed.
    rate_key = "login:user@example.com"
    for _ in range(10):
        resp = login(client, "user@example.com", "wrongpassword1")
        assert resp.status_code == 401
        _defuse_backoff(app, rate_key)
    resp = login(client, "user@example.com", "wrongpassword1")
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"


def test_repeated_bad_logins_trigger_exponential_backoff(client, outbox):
    """New in PART B: on top of the flat rate limit above, a run of failures
    close together triggers a growing lockout keyed to the account, distinct
    from (and reached well before) the flat limit."""
    _verify_user(client, outbox)
    for _ in range(5):
        resp = login(client, "user@example.com", "wrongpassword1")
        assert resp.status_code == 401

    resp = login(client, "user@example.com", "wrongpassword1")
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "account_locked"
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0

    # Even the *correct* password is refused while locked out -- this is a
    # lockout on the account, not just another failed-credentials check.
    resp = login(client, "user@example.com", "pw123456")
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "account_locked"


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


def test_forgot_password_is_rate_limited_per_email(client, outbox):
    """Same gap as resend-verification, same endpoint class: forgot-password
    is unauthenticated, sends a real email, and previously had no rate limit
    -- unbounded requests for one address either harasses that user or burns
    the shared Brevo quota for everyone. 3 requests succeed; the 4th is
    rejected before a reset email goes out."""
    _verify_user(client, outbox)
    outbox.clear()
    for _ in range(3):
        resp = client.post(
            "/api/auth/forgot-password",
            data=json.dumps({"email": "user@example.com"}),
            content_type="application/json",
            headers=JSON_HEADERS,
        )
        assert resp.status_code == 202
    assert len(outbox) == 3

    resp = client.post(
        "/api/auth/forgot-password",
        data=json.dumps({"email": "user@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"
    assert len(outbox) == 3


def test_forgot_password_is_rate_limited_per_ip_across_emails(client, outbox):
    """The per-email limit above wouldn't stop an attacker who spreads
    requests across many different (e.g. guessed or enumerated) target
    addresses from the same IP -- a separate per-IP counter bounds that. 5
    requests to 5 different addresses succeed (register()'s own IP limit is
    also 5/hour but keyed independently under "register_ip:", so it doesn't
    interfere here); the 6th, a 6th distinct address, is rejected."""
    for i in range(5):
        resp = client.post(
            "/api/auth/forgot-password",
            data=json.dumps({"email": "nobody{}@example.com".format(i)}),
            content_type="application/json",
            headers=JSON_HEADERS,
        )
        assert resp.status_code == 202

    resp = client.post(
        "/api/auth/forgot-password",
        data=json.dumps({"email": "nobody-sixth@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"


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


def test_reset_password_rejects_breached_password(client, outbox, monkeypatch):
    # See test_register_rejects_breached_password for why this patches
    # server.auth's bound name, not server.security's.
    import server.auth as auth_module

    _verify_user(client, outbox)
    outbox.clear()
    client.post(
        "/api/auth/forgot-password",
        data=json.dumps({"email": "user@example.com"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    reset_token = extract_token(outbox[-1]["text"])

    monkeypatch.setattr(auth_module, "is_password_breached", lambda pw: True)
    resp = client.post(
        "/api/auth/reset-password",
        data=json.dumps({"token": reset_token, "password": "newpassword1"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "password_breached"


def test_reset_password_checks_token_before_calling_hibp(client, monkeypatch):
    """Regression test for a DoS/quota-burning gap found in security review:
    reset-password used to call is_password_breached() (a real outbound HTTP
    request to HIBP) BEFORE looking up the token at all, so a garbage token
    with a syntactically-valid password still triggered a network call --
    meaning this *unauthenticated* endpoint could be hit with an unbounded
    stream of nonsense tokens purely to force outbound HTTP requests and tie
    up worker threads, no valid token or account knowledge required.

    The fix reorders token validation ahead of the breach check; assert the
    mechanism directly: with an invalid token, is_password_breached() must
    never be called, and the response is the token error, not a breach or
    HIBP-related one."""
    import server.auth as auth_module

    calls = []
    monkeypatch.setattr(auth_module, "is_password_breached", lambda pw: calls.append(pw) or False)

    resp = client.post(
        "/api/auth/reset-password",
        data=json.dumps({"token": "not-a-real-token", "password": "whatever123"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_token"
    assert calls == []  # HIBP was never reached for a token that was never valid


def test_reset_password_is_rate_limited_per_ip(client):
    """Defense in depth alongside the reordering above: this endpoint is
    unauthenticated, so bound the sheer number of attempts (valid token or
    not) any one source can make per hour, independent of which token or
    password is submitted."""
    for _ in range(20):
        resp = client.post(
            "/api/auth/reset-password",
            data=json.dumps({"token": "garbage-token", "password": "whatever123"}),
            content_type="application/json",
            headers=JSON_HEADERS,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_token"

    resp = client.post(
        "/api/auth/reset-password",
        data=json.dumps({"token": "garbage-token", "password": "whatever123"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"


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


def test_change_password_rejects_breached_password(client, outbox, monkeypatch):
    import server.auth as auth_module

    _verify_user(client, outbox)
    login(client, "user@example.com", "pw123456")

    monkeypatch.setattr(auth_module, "is_password_breached", lambda pw: True)
    resp = client.post(
        "/api/auth/change-password",
        data=json.dumps({"current_password": "pw123456", "new_password": "brandnew1"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "password_breached"


def test_change_password_requires_login(client):
    resp = client.post(
        "/api/auth/change-password",
        data=json.dumps({"current_password": "a", "new_password": "brandnew1"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 401
