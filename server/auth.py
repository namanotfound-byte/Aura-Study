"""Auth API blueprint -- registered at /api/auth by server/app.py.

Covers register (+ email verification), login/logout/me, resend-verification,
forgot/reset password and change-password. See spec section 6.
"""
import datetime

import flask

from .config import get_config
from .db import get_db, utcnow, utcnow_iso, parse_iso
from .mailer import send_verification_email, send_reset_email
from .security import (
    SESSION_COOKIE_NAME,
    count_attempts,
    create_session,
    current_user,
    delete_other_sessions,
    delete_session_by_token,
    email_looks_valid,
    hash_password,
    hash_token,
    json_error,
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


def _public_user(user) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "is_verified": bool(user["is_verified"]),
        "created_at": user["created_at"],
    }


def _issue_email_token(user_id: int, purpose: str, hours: int) -> str:
    """Invalidate any previous unused token of this purpose for the user, then
    issue a fresh single-use token and return the raw (unhashed) value."""
    db = get_db()
    now = utcnow()
    db.execute(
        "UPDATE email_tokens SET used_at = ? WHERE user_id = ? AND purpose = ? AND used_at IS NULL",
        (now.isoformat(), user_id, purpose),
    )
    raw = new_token()
    expires = now + datetime.timedelta(hours=hours)
    db.execute(
        "INSERT INTO email_tokens (user_id, token_hash, purpose, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, hash_token(raw), purpose, now.isoformat(), expires.isoformat()),
    )
    db.commit()
    return raw


REGISTER_MESSAGE = "Almost there, smartiepants! Check your email to confirm your account."


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

    record_attempt(ip_key)

    generic = (flask.jsonify({"ok": True, "message": REGISTER_MESSAGE}), 202)

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing is not None:
        # Enumeration resistance: identical response whether or not the email exists.
        return generic

    cfg = get_config()
    now = utcnow_iso()
    cur = db.execute(
        "INSERT INTO users (email, password_hash, display_name, is_verified, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (email, hash_password(password), display_name, 0 if cfg.require_email_verification else 1, now),
    )
    db.commit()
    user_id = cur.lastrowid

    if cfg.require_email_verification:
        raw = _issue_email_token(user_id, "verify", hours=24)
        send_verification_email(email, raw)

    return generic


@bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    require_csrf()
    data = flask.request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    generic = (flask.jsonify({"ok": True}), 202)
    if not email:
        return generic

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
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

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user is None or not verify_password(password, user["password_hash"]):
        record_attempt(rate_key)
        return json_error("invalid_credentials", "Incorrect email or password.", 401)

    cfg = get_config()
    if cfg.require_email_verification and not user["is_verified"]:
        return json_error("email_unverified", "Please verify your email before logging in.", 403)

    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utcnow_iso(), user["id"]))
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
        "SELECT 1 FROM spotify_accounts WHERE user_id = ?", (user["id"],)
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

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
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
    pw_err = password_policy_error(password)
    if pw_err:
        return json_error("validation_error", pw_err, 400)

    db = get_db()
    token_row = db.execute(
        "SELECT * FROM email_tokens WHERE token_hash = ? AND purpose = 'reset'",
        (hash_token(raw_token),),
    ).fetchone()
    if (token_row is None or token_row["used_at"] is not None
            or parse_iso(token_row["expires_at"]) <= utcnow()):
        return json_error("invalid_token", "This reset link is invalid or has expired.", 400)

    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(password), token_row["user_id"]),
    )
    db.execute("UPDATE email_tokens SET used_at = ? WHERE id = ?", (utcnow_iso(), token_row["id"]))
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

    db = get_db()
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(new_password), user["id"]),
    )
    db.commit()

    current_token = flask.request.cookies.get(SESSION_COOKIE_NAME)
    delete_other_sessions(user["id"], keep_raw_token=current_token)

    return flask.jsonify({"ok": True})
