"""Spotify access-request collection.

While a Spotify app is in Development Mode, only accounts explicitly listed
in that app's User Management (max 25) can authorise against it -- so a
user of AuraStudy cannot connect Spotify until the app owner adds their
Spotify account email by hand in Spotify's own dashboard. This module lets a
user submit that email and check/withdraw their own submission; the owner's
list view lives in server/app.py (GET /admin/spotify-requests), which
imports the query helpers below rather than duplicating them.

Blueprint 'spotify_requests', mounted by server/app.py at the same
url_prefix="/api/spotify" as server/spotify.py's OAuth blueprint -- the
route paths here (/access-request) don't collide with that blueprint's.

Frozen API contract (coded against by the frontend agent's form):
    GET    /api/spotify/access-request -> {"submitted", "spotify_email",
                                            "status", "submitted_at"}
    POST   /api/spotify/access-request {"spotify_email"} -> {"ok","status"}
    DELETE /api/spotify/access-request -> {"ok": true}
All @login_required, CSRF header required on POST/DELETE. One request per
user -- a second POST updates the existing row (and resets it to "pending",
since the owner hasn't yet added whatever the *new* email is) rather than
creating another.

Privacy: a user's Spotify email is personal data being handed to another
person (the owner, so they can add it to Spotify's dashboard). Nothing in
this module logs it -- every log-shaped string below is user-id/request-id
only. It must never be exposed on any endpoint besides the owner's list view
and that same user's own GET.
"""
from flask import Blueprint, g, jsonify, request

from .db import get_db, iso_or_none, utcnow_iso
from .security import (
    count_attempts,
    email_looks_valid,
    json_error,
    login_required,
    record_attempt,
    require_csrf,
)

bp = Blueprint("spotify_requests", __name__)

# Rate-limits POST /access-request per logged-in user, using the same
# auth_attempts mechanism the rest of the app already rate-limits with (see
# server/security.py record_attempt/count_attempts). Keyed by user id, not
# IP -- the endpoint is already @login_required, so the account itself is
# the meaningful identity to bound, and an IP-based key would do nothing to
# stop one account hammering it from many addresses.
SUBMIT_RATE_LIMIT = 8
SUBMIT_RATE_WINDOW_MINUTES = 60


def _rate_key(user_id) -> str:
    return "spotify_access_request:{}".format(user_id)


def _row_for_user(db, user_id):
    return db.execute(
        "SELECT * FROM spotify_access_requests WHERE user_id = %s", (user_id,)
    ).fetchone()


def _serialize_own(row) -> dict:
    if row is None:
        return {"submitted": False, "spotify_email": None, "status": None, "submitted_at": None}
    return {
        "submitted": True,
        "spotify_email": row["spotify_email"],
        "status": row["status"],
        "submitted_at": iso_or_none(row["created_at"]),
    }


# ---------------------------------------------------------------------------
# Owner-only query helpers -- callers (server/app.py's /admin/spotify-requests
# routes) MUST have already verified the current user is the configured
# owner before calling either of these. Neither function itself checks that
# -- they're plain data access, the access-control decision lives in app.py.
# ---------------------------------------------------------------------------

def list_all_requests(db):
    """Every spotify_access_requests row, joined to the submitting user's
    own account email/display name, pending requests first then oldest
    first within each group. OWNER-ONLY data."""
    return db.execute(
        """
        SELECT r.id, r.spotify_email, r.status, r.created_at, r.updated_at,
               u.email AS user_email, u.display_name AS user_display_name
        FROM spotify_access_requests r
        JOIN users u ON u.id = r.user_id
        ORDER BY (r.status = 'pending') DESC, r.created_at ASC
        """
    ).fetchall()


def mark_request_added(db, request_id) -> bool:
    """Sets one request's status to 'added'. OWNER-ONLY action. Returns True
    if a row was updated, False if request_id doesn't exist."""
    cur = db.execute(
        "UPDATE spotify_access_requests SET status = 'added', updated_at = %s WHERE id = %s",
        (utcnow_iso(), request_id),
    )
    db.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/access-request", methods=["GET"])
@login_required
def get_access_request():
    db = get_db()
    row = _row_for_user(db, g.user["id"])
    return jsonify(_serialize_own(row))


@bp.route("/access-request", methods=["POST"])
@login_required
def submit_access_request():
    require_csrf()

    rate_key = _rate_key(g.user["id"])
    if count_attempts(rate_key, since_minutes=SUBMIT_RATE_WINDOW_MINUTES) >= SUBMIT_RATE_LIMIT:
        return json_error("rate_limited", "Too many requests. Please try again later.", 429)
    record_attempt(rate_key)

    data = request.get_json(silent=True) or {}
    spotify_email = (data.get("spotify_email") or "").strip()
    if not email_looks_valid(spotify_email):
        return json_error("validation_error", "Please enter a valid Spotify account email.", 400)

    db = get_db()
    now = utcnow_iso()
    # ON CONFLICT(user_id) DO UPDATE -- "one request per user": a second
    # submission replaces the email and resets status back to 'pending'
    # (even if the previous one had already been marked 'added' -- the
    # owner hasn't added *this* email yet), rather than inserting a second
    # row. Matches the upsert pattern server/spotify.py already uses for
    # spotify_accounts.
    db.execute(
        """
        INSERT INTO spotify_access_requests (user_id, spotify_email, status, created_at, updated_at)
        VALUES (%s, %s, 'pending', %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            spotify_email = excluded.spotify_email,
            status = 'pending',
            updated_at = excluded.updated_at
        """,
        (g.user["id"], spotify_email, now, now),
    )
    db.commit()
    return jsonify({"ok": True, "status": "pending"})


@bp.route("/access-request", methods=["DELETE"])
@login_required
def withdraw_access_request():
    require_csrf()
    db = get_db()
    db.execute("DELETE FROM spotify_access_requests WHERE user_id = %s", (g.user["id"],))
    db.commit()
    return jsonify({"ok": True})
