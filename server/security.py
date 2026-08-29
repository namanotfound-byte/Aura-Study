"""Password hashing, tokens, session/auth decorators, CSRF and rate-limit
helpers. See spec section 5 and the frozen contract in section 13.
"""
import base64
import datetime
import functools
import hashlib
import hmac
import re
import secrets
from typing import Optional
from urllib.parse import quote

import flask
from cryptography.fernet import Fernet

from .config import get_config
from .db import get_db, utcnow, utcnow_iso, parse_iso

PBKDF2_ITERATIONS = 240000
SESSION_COOKIE_NAME = "aurastudy_session"
SESSION_LIFETIME_DAYS = 30

MAX_PASSWORD_LENGTH = 200
MIN_PASSWORD_LENGTH = 8

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ApiError(Exception):
    """Raised by `require_csrf()` (and usable elsewhere) to short-circuit a
    view with a spec-shaped JSON error. Caught by an app-wide error handler."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------- passwords

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(pw: str, stored: str) -> bool:
    try:
        scheme, iterations_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def password_policy_error(pw: str) -> Optional[str]:
    """Returns a human-readable error string, or None if the password is fine."""
    if len(pw) > MAX_PASSWORD_LENGTH:
        return "Password is too long."
    if len(pw) < MIN_PASSWORD_LENGTH:
        return "Password must be at least {} characters.".format(MIN_PASSWORD_LENGTH)
    if not re.search(r"[A-Za-z]", pw):
        return "Password must contain at least one letter."
    if not re.search(r"[0-9]", pw):
        return "Password must contain at least one digit."
    return None


def email_looks_valid(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(_EMAIL_RE.match(email))


# -------------------------------------------------------------------- tokens

def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ sessions

def create_session(user_id: int, user_agent: Optional[str]) -> str:
    raw = new_token()
    db = get_db()
    now = utcnow()
    expires = now + datetime.timedelta(days=SESSION_LIFETIME_DAYS)
    db.execute(
        "INSERT INTO sessions (user_id, token_hash, created_at, expires_at, user_agent) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, hash_token(raw), now.isoformat(), expires.isoformat(), user_agent),
    )
    db.commit()
    return raw


def delete_session_by_token(raw: str) -> None:
    db = get_db()
    db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(raw),))
    db.commit()


def delete_other_sessions(user_id: int, keep_raw_token: Optional[str] = None) -> None:
    """Revoke all sessions for a user, optionally keeping the current one (used
    by change-password, which the spec requires to revoke *other* sessions)."""
    db = get_db()
    if keep_raw_token:
        db.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
            (user_id, hash_token(keep_raw_token)),
        )
    else:
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    db.commit()


def set_session_cookie(response: flask.Response, raw_token: str) -> None:
    cfg = get_config()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_token,
        max_age=60 * 60 * 24 * SESSION_LIFETIME_DAYS,
        httponly=True,
        samesite="Lax",
        path="/",
        secure=cfg.app_base_url.startswith("https"),
    )


def clear_session_cookie(response: flask.Response) -> None:
    cfg = get_config()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        "",
        max_age=0,
        httponly=True,
        samesite="Lax",
        path="/",
        secure=cfg.app_base_url.startswith("https"),
    )


def _resolve_user_from_cookie():
    token = flask.request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    db = get_db()
    session_row = db.execute(
        "SELECT * FROM sessions WHERE token_hash = ?", (hash_token(token),)
    ).fetchone()
    if session_row is None:
        return None
    if parse_iso(session_row["expires_at"]) <= utcnow():
        db.execute("DELETE FROM sessions WHERE id = ?", (session_row["id"],))
        db.commit()
        return None
    return db.execute(
        "SELECT * FROM users WHERE id = ?", (session_row["user_id"],)
    ).fetchone()


def current_user():
    """-> Optional[sqlite3.Row]. Resolves (and caches on flask.g) the user for
    the current request's session cookie, independent of `login_required`."""
    if "user" not in flask.g:
        flask.g.user = _resolve_user_from_cookie()
    return flask.g.user


def json_error(code: str, message: str, status: int):
    return flask.jsonify({"error": code, "message": message}), status


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        is_api = flask.request.path.startswith("/api/")
        user = current_user()
        if user is None:
            if is_api:
                return json_error("unauthenticated", "You need to be logged in.", 401)
            return flask.redirect("/login?next=" + quote(flask.request.path, safe=""))

        cfg = get_config()
        if cfg.require_email_verification and not user["is_verified"]:
            if is_api:
                return json_error("email_unverified", "Please verify your email first.", 403)
            return flask.redirect("/login?next=" + quote(flask.request.path, safe=""))

        flask.g.user = user
        return fn(*args, **kwargs)

    return wrapper


# ----------------------------------------------------------------------- csrf

def require_csrf() -> None:
    if flask.request.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise ApiError("csrf_failed", "Missing required X-Requested-With header.", 403)


# ------------------------------------------------------------------ rate limit

def record_attempt(key: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO auth_attempts (key, created_at) VALUES (?, ?)", (key, utcnow_iso())
    )
    cutoff = (utcnow() - datetime.timedelta(hours=24)).isoformat()
    db.execute("DELETE FROM auth_attempts WHERE created_at < ?", (cutoff,))
    db.commit()


def count_attempts(key: str, since_minutes: int) -> int:
    db = get_db()
    cutoff = (utcnow() - datetime.timedelta(minutes=since_minutes)).isoformat()
    row = db.execute(
        "SELECT COUNT(*) AS c FROM auth_attempts WHERE key = ? AND created_at >= ?",
        (key, cutoff),
    ).fetchone()
    return row["c"]


# ------------------------------------------------------------- token crypto

def _fernet() -> Fernet:
    return Fernet(get_config().token_enc_key.encode("utf-8"))


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
