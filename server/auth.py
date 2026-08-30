"""Auth API blueprint -- registered at /api/auth by server/app.py.

Covers register (+ email verification), login/logout/me, resend-verification,
forgot/reset password and change-password. See spec section 6.
"""
import datetime
import secrets

import flask

from .config import get_config
from .db import get_db, iso_or_none, utcnow, utcnow_iso, parse_iso
from .mailer import send_verification_email, send_reset_email
from .security import (
    INTEGRITY_ERRORS,
    SESSION_COOKIE_NAME,
    count_attempts,
    create_session,
    current_user,
    delete_other_sessions,
    delete_session_by_token,
    email_looks_valid,
    hash_password,
    hash_token,
    is_password_breached,
    json_error,
    lockout_seconds_remaining,
    login_required,
    new_token,
    password_policy_error,
    record_attempt,
    require_csrf,
    set_session_cookie,
    clear_session_cookie,
    verify_password,
)

bp = flask.Blueprint("auth", __name__)

# A fixed, valid-shaped password hash that login() below runs verify_password()
# against when no user row matches the submitted email. Without this, a wrong
# password for a *real* account pays the full PBKDF2-240k cost inside
# verify_password(), while a nonexistent email short-circuits before ever
# calling it -- a measurable timing difference (hundreds of ms of PBKDF2 vs.
# a single indexed SELECT) that lets an attacker enumerate registered emails
# via response time alone, even though the response *body* is identical
# ("invalid_credentials" either way). Computed once at import time so the
# per-request cost is just the one PBKDF2 call, same as a real failed check.
_DUMMY_PASSWORD_HASH = None


def _dummy_password_hash() -> str:
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))
    return _DUMMY_PASSWORD_HASH


def _public_user(user) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "is_verified": bool(user["is_verified"]),
        # user["created_at"] is a datetime on Postgres, an ISO string on
        # SQLite -- normalise to ISO-8601 either way (see db.iso_or_none).
        "created_at": iso_or_none(user["created_at"]),
    }


def _issue_email_token(user_id: int, purpose: str, hours: int, commit: bool = True) -> str:
    """Invalidate any previous unused token of this purpose for the user, then
    issue a fresh single-use token and return the raw (unhashed) value.

    `commit=False` lets a caller (register()) fold this into a larger
    transaction so a user row is never committed without its verification
    token, or vice versa -- see the "half-created user" warning in
    SPEC-PHASE3.md PART A.
    """
    db = get_db()
    now = utcnow()
    db.execute(
        "UPDATE email_tokens SET used_at = %s WHERE user_id = %s AND purpose = %s AND used_at IS NULL",
        (now.isoformat(), user_id, purpose),
    )
    raw = new_token()
    expires = now + datetime.timedelta(hours=hours)
    db.execute(
        "INSERT INTO email_tokens (user_id, token_hash, purpose, created_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user_id, hash_token(raw), purpose, now.isoformat(), expires.isoformat()),
    )
    if commit:
        db.commit()
    return raw


REGISTER_MESSAGE = "Almost there! Check your email to confirm your account."


def _user_exists(db, email: str) -> bool:
    return db.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone() is not None


def _mail_send_allowed(kind: str, email: str) -> bool:
    """Rate-guards an unauthenticated endpoint that triggers a real outbound
    email (forgot-password, resend-verification). Neither had ANY limit
    before this: an attacker could script an unbounded loop of requests --
    each one a real SMTP send through Brevo's 300/day free quota -- to
    either exhaust the whole app's daily email allowance (breaking
    verification/reset for every user) or simply spam one victim's inbox by
    repeatedly targeting their address. Two independent keys, both counted
    against the same `auth_attempts` table register() already uses:
    per-IP (bounds a single attacker's overall volume) and per-email (bounds
    how many times any one address can be targeted, even from many IPs).
    """
    ip_key = "{}_ip:{}".format(kind, flask.request.remote_addr or "unknown")
    email_key = "{}_email:{}".format(kind, email)
    allowed = count_attempts(ip_key, since_minutes=60) < 5 and count_attempts(email_key, since_minutes=60) < 3
    record_attempt(ip_key)
    record_attempt(email_key)
    return allowed


@bp.route("/register", methods=["POST"])
def register():
    require_csrf()
    data = flask.request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip() or None

    ip_key = "register_ip:{}".format(flask.request.remote_addr or "unknown")
    if count_attempts(ip_key, since_minutes=60) >= 5:
        return json_error("rate_limited", "Too many registrations from this address. Try again later.", 429)

    if not email_looks_valid(email):
        return json_error("validation_error", "Please enter a valid email address.", 400)
    pw_err = password_policy_error(password)
    if pw_err:
        return json_error("validation_error", pw_err, 400)

    # Count the attempt as soon as the request is a plausible registration
    # (valid email shape, password meets the policy) and *before* the HIBP
    # breach check below -- HIBP is an outbound network call, and if the
    # counter were only incremented on the fully-successful path, an
    # attacker could send an endless stream of requests using a common
    # breached password (trivial to pick) and never trip the 5/hour limit,
    # since every one of those requests would 400 out on the breach check
    # ahead of where record_attempt() used to sit. That turns this endpoint
    # into an unlimited, unauthenticated way to force outbound HIBP calls
    # and tie up worker threads. Counting here closes that gap.
    record_attempt(ip_key)

    if is_password_breached(password):
        return json_error(
            "password_breached",
            "That password has appeared in a known data breach. Please choose a different one.",
            400,
        )

    generic = (flask.jsonify({"ok": True, "message": REGISTER_MESSAGE}), 202)

    db = get_db()
    if _user_exists(db, email):
        # Enumeration resistance: identical response whether or not the email exists.
        return generic

    cfg = get_config()
    now = utcnow_iso()
    try:
        # is_verified is a real BOOLEAN column on Postgres -- bind a Python
        # bool, not 0/1 (Postgres rejects an integer literal/parameter for a
        # boolean column outright; SQLite is happy to store either).
        cur = db.execute(
            "INSERT INTO users (email, password_hash, display_name, is_verified, created_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (email, hash_password(password), display_name, not cfg.require_email_verification, now),
        )
        user_id = cur.fetchone()["id"]

        # Insert the user row and (if required) its verification token in
        # one transaction: a failure between the two must not leave a user
        # that can never verify. Only commit once both writes are staged.
        if cfg.require_email_verification:
            raw = _issue_email_token(user_id, "verify", hours=24, commit=False)
            db.commit()
            send_verification_email(email, raw)
        else:
            db.commit()
    except INTEGRITY_ERRORS:
        # The `_user_exists` check above and this INSERT aren't atomic: two
        # concurrent registrations for the same email can both pass the
        # check before either commits, and the loser hits the database's own
        # `lower(email)` uniqueness constraint here instead. That must land
        # on the exact same generic response as "email already registered",
        # not an unhandled 500 -- and, since the response is identical
        # either way, this can't be used to detect the race either.
        db.rollback()
        return generic

    return generic


@bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    require_csrf()
    data = flask.request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    generic = (flask.jsonify({"ok": True}), 202)
    if not email:
        return generic
    if not _mail_send_allowed("resend", email):
        return json_error("rate_limited", "Too many requests. Please try again later.", 429)

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    if user is not None and not user["is_verified"]:
        raw = _issue_email_token(user["id"], "verify", hours=24)
        send_verification_email(email, raw)
    return generic


@bp.route("/login", methods=["POST"])
def login():
    require_csrf()
    data = flask.request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return json_error("validation_error", "Email and password are required.", 400)

    rate_key = "login:{}".format(email)
    if count_attempts(rate_key, since_minutes=15) >= 10:
        return json_error("rate_limited", "Too many attempts. Please try again in a bit.", 429)

    # Exponential backoff on top of the flat limit above (see
    # security.py:lockout_seconds_remaining) -- checked before touching the
    # password hash at all, so a locked-out account doesn't even pay the
    # PBKDF2 cost per attempt.
    wait = lockout_seconds_remaining(rate_key)
    if wait > 0:
        resp, status = json_error(
            "account_locked",
            "Too many failed attempts. Please try again in {}s.".format(wait),
            429,
        )
        resp.headers["Retry-After"] = str(wait)
        return resp, status

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    if user is None:
        # Pay the same PBKDF2 cost a real failed check would -- see
        # _dummy_password_hash() -- so this branch isn't distinguishable
        # from a wrong-password branch by response time.
        verify_password(password, _dummy_password_hash())
        record_attempt(rate_key)
        return json_error("invalid_credentials", "Incorrect email or password.", 401)
    if not verify_password(password, user["password_hash"]):
        record_attempt(rate_key)
        return json_error("invalid_credentials", "Incorrect email or password.", 401)

    cfg = get_config()
    if cfg.require_email_verification and not user["is_verified"]:
        return json_error("email_unverified", "Please verify your email before logging in.", 403)

    db.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (utcnow_iso(), user["id"]))
    db.commit()

    raw = create_session(user["id"], flask.request.headers.get("User-Agent"))
    resp = flask.jsonify({"ok": True, "user": _public_user(user)})
    set_session_cookie(resp, raw)
    return resp


@bp.route("/logout", methods=["POST"])
def logout():
    require_csrf()
    token = flask.request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        delete_session_by_token(token)
    resp = flask.jsonify({"ok": True})
    clear_session_cookie(resp)
    return resp


@bp.route("/me", methods=["GET"])
def me():
    user = current_user()
    if user is None:
        return json_error("unauthenticated", "Not logged in.", 401)
    db = get_db()
    spotify_row = db.execute(
        "SELECT 1 FROM spotify_accounts WHERE user_id = %s", (user["id"],)
    ).fetchone()
    return flask.jsonify({
        "user": _public_user(user),
        "spotify_connected": spotify_row is not None,
    })


@bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    require_csrf()
    data = flask.request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    generic = (flask.jsonify({"ok": True}), 202)
    if not email:
        return generic
    if not _mail_send_allowed("forgot", email):
        return json_error("rate_limited", "Too many requests. Please try again later.", 429)

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    if user is not None:
        raw = _issue_email_token(user["id"], "reset", hours=1)
        send_reset_email(email, raw)
    return generic


@bp.route("/reset-password", methods=["POST"])
def reset_password():
    require_csrf()
    data = flask.request.get_json(silent=True) or {}
    raw_token = data.get("token") or ""
    password = data.get("password") or ""

    if not raw_token:
        return json_error("validation_error", "Missing token.", 400)

    # This endpoint is unauthenticated, so an IP-based limit bounds how many
    # attempts (valid or garbage token) it will even entertain per hour --
    # defense in depth on top of the reordering below.
    ip_key = "reset_ip:{}".format(flask.request.remote_addr or "unknown")
    if count_attempts(ip_key, since_minutes=60) >= 20:
        return json_error("rate_limited", "Too many attempts. Please try again later.", 429)
    record_attempt(ip_key)

    # Validate the token FIRST, before the password policy/breach checks.
    # is_password_breached() below makes a real outbound HTTP call to HIBP;
    # it used to run before the token was even looked up, so any POST with a
    # syntactically-valid password -- garbage token or not -- triggered a
    # network call. Since a *valid* token requires having actually received
    # the reset email, checking it first (a cheap DB read) turns "spam this
    # endpoint to burn HIBP calls" from trivial into "first go steal
    # someone's reset email", which the rate limit above also now bounds.
    db = get_db()
    token_row = db.execute(
        "SELECT * FROM email_tokens WHERE token_hash = %s AND purpose = 'reset'",
        (hash_token(raw_token),),
    ).fetchone()
    if (token_row is None or token_row["used_at"] is not None
            or parse_iso(token_row["expires_at"]) <= utcnow()):
        return json_error("invalid_token", "This reset link is invalid or has expired.", 400)

    pw_err = password_policy_error(password)
    if pw_err:
        return json_error("validation_error", pw_err, 400)
    if is_password_breached(password):
        return json_error(
            "password_breached",
            "That password has appeared in a known data breach. Please choose a different one.",
            400,
        )

    db.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (hash_password(password), token_row["user_id"]),
    )
    db.execute("UPDATE email_tokens SET used_at = %s WHERE id = %s", (utcnow_iso(), token_row["id"]))
    db.commit()
    delete_other_sessions(token_row["user_id"])  # a reset password invalidates every session

    return flask.jsonify({"ok": True})


@bp.route("/change-password", methods=["POST"])
@login_required
def change_password():
    require_csrf()
    data = flask.request.get_json(silent=True) or {}
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""

    user = flask.g.user
    if not verify_password(current_password, user["password_hash"]):
        return json_error("invalid_credentials", "Current password is incorrect.", 401)
    pw_err = password_policy_error(new_password)
    if pw_err:
        return json_error("validation_error", pw_err, 400)
    if is_password_breached(new_password):
        return json_error(
            "password_breached",
            "That password has appeared in a known data breach. Please choose a different one.",
            400,
        )

    db = get_db()
    db.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (hash_password(new_password), user["id"]),
    )
    db.commit()

    current_token = flask.request.cookies.get(SESSION_COOKIE_NAME)
    delete_other_sessions(user["id"], keep_raw_token=current_token)

    return flask.jsonify({"ok": True})
