"""Password hashing, tokens, session/auth decorators, CSRF and rate-limit
helpers. See spec section 5 and the frozen contract in section 13.
"""
import base64
import datetime
import functools
import hashlib
import hmac
import logging
import re
import secrets
import sqlite3
from typing import Optional
from urllib.parse import quote

import flask
import requests
from cryptography.fernet import Fernet

from .config import get_config
from .db import get_db, utcnow, utcnow_iso, parse_iso

PBKDF2_ITERATIONS = 240000
SESSION_COOKIE_NAME = "aurastudy_session"
SESSION_LIFETIME_DAYS = 30

MAX_PASSWORD_LENGTH = 200
MIN_PASSWORD_LENGTH = 8

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_log = logging.getLogger("aurastudy.security")

# psycopg is a hard dependency (requirements.txt), but keep this import
# defensive rather than assuming it -- a missing/broken psycopg install
# should surface as an ImportError where it's actually used (server/db.py),
# not here.
try:
    import psycopg

    INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)
except ImportError:  # pragma: no cover - psycopg is always installed per requirements.txt
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)


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
        "VALUES (%s, %s, %s, %s, %s)",
        (user_id, hash_token(raw), now.isoformat(), expires.isoformat(), user_agent),
    )
    db.commit()
    return raw


def delete_session_by_token(raw: str) -> None:
    db = get_db()
    db.execute("DELETE FROM sessions WHERE token_hash = %s", (hash_token(raw),))
    db.commit()


def delete_other_sessions(user_id: int, keep_raw_token: Optional[str] = None) -> None:
    """Revoke all sessions for a user, optionally keeping the current one (used
    by change-password, which the spec requires to revoke *other* sessions)."""
    db = get_db()
    if keep_raw_token:
        db.execute(
            "DELETE FROM sessions WHERE user_id = %s AND token_hash != %s",
            (user_id, hash_token(keep_raw_token)),
        )
    else:
        db.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
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
        "SELECT * FROM sessions WHERE token_hash = %s", (hash_token(token),)
    ).fetchone()
    if session_row is None:
        return None
    if parse_iso(session_row["expires_at"]) <= utcnow():
        db.execute("DELETE FROM sessions WHERE id = %s", (session_row["id"],))
        db.commit()
        return None
    return db.execute(
        "SELECT * FROM users WHERE id = %s", (session_row["user_id"],)
    ).fetchone()


def current_user():
    """-> Optional mapping-like row (sqlite3.Row on SQLite, a dict via
    psycopg's dict_row on Postgres). Resolves (and caches on flask.g) the
    user for the current request's session cookie, independent of
    `login_required`."""
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
        "INSERT INTO auth_attempts (key, created_at) VALUES (%s, %s)", (key, utcnow_iso())
    )
    cutoff = (utcnow() - datetime.timedelta(hours=24)).isoformat()
    db.execute("DELETE FROM auth_attempts WHERE created_at < %s", (cutoff,))
    db.commit()


def count_attempts(key: str, since_minutes: int) -> int:
    db = get_db()
    cutoff = (utcnow() - datetime.timedelta(minutes=since_minutes)).isoformat()
    row = db.execute(
        "SELECT COUNT(*) AS c FROM auth_attempts WHERE key = %s AND created_at >= %s",
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


# --------------------------------------------------------- breached passwords

HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/{}"
HIBP_TIMEOUT_SECONDS = 3


def is_password_breached(password: str) -> bool:
    """Have I Been Pwned k-anonymity check: only the first 5 hex characters
    of the password's SHA-1 hash are ever sent -- never the password, never
    the full hash. HIBP returns every suffix sharing that prefix (with a
    count), and the match happens locally.

    Fails OPEN: any network error, timeout, or non-2xx response logs a
    warning and returns False (not breached) rather than blocking the
    caller -- a third-party outage must never prevent registration/reset.
    """
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    try:
        resp = requests.get(
            HIBP_RANGE_URL.format(prefix),
            timeout=HIBP_TIMEOUT_SECONDS,
            # Asks HIBP to pad the response with decoy entries so a passive
            # network observer can't infer the k-anonymity set size (and
            # thus narrow down the password) from the response length alone.
            headers={"Add-Padding": "true"},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        _log.warning("HIBP breached-password check unreachable, failing open: %s", exc)
        return False

    for line in resp.text.splitlines():
        candidate, _, _count = line.strip().partition(":")
        if candidate == suffix:
            return True
    return False


# ------------------------------------------------------------ login lockout

# Layered on top of the flat "10 failed logins per email per 15 minutes ->
# 429 rate_limited" check already in auth.py:login(). This adds a *growing*
# delay between attempts as failures accumulate, so an attacker can't simply
# burn through the flat allowance as fast as the network allows -- while a
# genuine user who mistypes a password 2-3 times sees no friction at all.
LOCKOUT_THRESHOLD = 5          # backoff engages once this many recent failures exist
LOCKOUT_WINDOW_MINUTES = 2     # only failures this fresh count toward backoff
LOCKOUT_BASE_SECONDS = 2
LOCKOUT_MAX_SECONDS = 5 * 60   # cap so a very old burst can't lock an account out indefinitely


def lockout_seconds_remaining(rate_key: str) -> int:
    """Seconds the caller must still wait before another login attempt for
    this `rate_key` (see auth.py's "login:<email>" keys), or 0 if not
    currently locked out. Reads the same `auth_attempts` table that
    record_attempt()/count_attempts() already write -- no separate storage.

    Backoff = min(BASE * 2^(fails - THRESHOLD), MAX) seconds since the most
    recent failure, where `fails` is the count of failures within the last
    LOCKOUT_WINDOW_MINUTES. Older failures age out of the window and stop
    counting, so a stale burst from an hour ago doesn't lock anyone out now.
    """
    db = get_db()
    window_cutoff = (utcnow() - datetime.timedelta(minutes=LOCKOUT_WINDOW_MINUTES)).isoformat()
    rows = db.execute(
        "SELECT created_at FROM auth_attempts WHERE key = %s AND created_at >= %s "
        "ORDER BY created_at DESC",
        (rate_key, window_cutoff),
    ).fetchall()
    fails = len(rows)
    if fails < LOCKOUT_THRESHOLD:
        return 0

    backoff = min(LOCKOUT_BASE_SECONDS * (2 ** (fails - LOCKOUT_THRESHOLD)), LOCKOUT_MAX_SECONDS)
    last_fail = parse_iso(rows[0]["created_at"])
    elapsed = (utcnow() - last_fail).total_seconds()
    remaining = backoff - elapsed
    return int(remaining) + 1 if remaining > 0 else 0
