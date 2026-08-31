"""Doubts / support inbox.

Blueprint 'support', mounted by server/app.py at url_prefix="/api/support" --
the frozen, user-facing contract (coded against by the frontend agent):

    GET  /api/support/messages -> {"messages":[{id,body,from_admin,
                                    created_at}], "unread_replies": int}
    POST /api/support/messages {"body"} -> 201 {"ok": true}
    POST /api/support/read -> {"ok": true}   (marks admin replies read)

All @login_required, CSRF header required on POST. One conversation per
user -- no threading, no subjects -- so `support_messages` (server/db.py) is
a single flat table per user ordered by `created_at`.

The owner-side admin page (GET /admin/support, POST
/admin/support/<user_id>/reply) lives in server/app.py, which imports the
query/write helpers below rather than duplicating them -- same split as
server/spotify_requests.py. Those helpers are OWNER-ONLY data access: the
access-control decision (is the caller actually the configured owner) is
made in app.py before any of them are called, not here.

Message bodies are user-written text that gets rendered back into another
person's browser (the owner's admin page, or the sender's own inbox view).
Nothing in this module (or the admin templates) marks it `|safe` -- Jinja's
default autoescaping is what makes that safe, not any cleverness here.
"""
import flask

from .db import get_db, iso_or_none, utcnow_iso
from .security import count_attempts, json_error, login_required, record_attempt, require_csrf

bp = flask.Blueprint("support", __name__)

MIN_BODY_LENGTH = 1
MAX_BODY_LENGTH = 2000

# Rate-limits POST /api/support/messages per logged-in user, via the same
# auth_attempts mechanism the rest of the app already rate-limits with (see
# server/security.py record_attempt/count_attempts, and the identical
# pattern in server/spotify_requests.py). Keyed by user id, not IP, since
# the endpoint is already @login_required -- the account is the meaningful
# identity to bound here.
SEND_RATE_LIMIT = 20
SEND_RATE_WINDOW_MINUTES = 60


def _rate_key(user_id) -> str:
    return "support_message:{}".format(user_id)


def validate_body(raw):
    """Returns (cleaned_text, error:None) or (None, error:str). 1-2000
    characters after trimming -- empty or oversized is rejected outright,
    matching the spec's 400 validation_error contract."""
    if not isinstance(raw, str):
        return None, "Message is required."
    value = raw.strip()
    if len(value) < MIN_BODY_LENGTH:
        return None, "Message can't be empty."
    if len(value) > MAX_BODY_LENGTH:
        return None, "Message is too long (max {} characters).".format(MAX_BODY_LENGTH)
    return value, None


# ---------------------------------------------------------------------------
# User-facing API
# ---------------------------------------------------------------------------

@bp.route("/messages", methods=["GET"])
@login_required
def get_messages():
    db = get_db()
    user_id = flask.g.user["id"]

    rows = db.execute(
        "SELECT id, body, from_admin, created_at FROM support_messages "
        "WHERE user_id = %s ORDER BY created_at ASC",
        (user_id,),
    ).fetchall()
    messages = [
        {
            "id": r["id"],
            "body": r["body"],
            "from_admin": bool(r["from_admin"]),
            "created_at": iso_or_none(r["created_at"]),
        }
        for r in rows
    ]

    unread_row = db.execute(
        "SELECT COUNT(*) AS c FROM support_messages "
        "WHERE user_id = %s AND from_admin = %s AND read_at IS NULL",
        (user_id, True),
    ).fetchone()

    return flask.jsonify({"messages": messages, "unread_replies": unread_row["c"]})


@bp.route("/messages", methods=["POST"])
@login_required
def post_message():
    require_csrf()
    user_id = flask.g.user["id"]

    rate_key = _rate_key(user_id)
    if count_attempts(rate_key, since_minutes=SEND_RATE_WINDOW_MINUTES) >= SEND_RATE_LIMIT:
        return json_error("rate_limited", "Too many messages. Please try again later.", 429)
    record_attempt(rate_key)

    body = flask.request.get_json(silent=True)
    if not isinstance(body, dict):
        return json_error("validation_error", "Request body must be a JSON object.", 400)

    text, err = validate_body(body.get("body"))
    if err:
        return json_error("validation_error", err, 400)

    db = get_db()
    db.execute(
        "INSERT INTO support_messages (user_id, body, from_admin, created_at) "
        "VALUES (%s, %s, %s, %s)",
        (user_id, text, False, utcnow_iso()),
    )
    db.commit()
    return flask.jsonify({"ok": True}), 201


@bp.route("/read", methods=["POST"])
@login_required
def mark_read():
    require_csrf()
    db = get_db()
    user_id = flask.g.user["id"]
    db.execute(
        "UPDATE support_messages SET read_at = %s "
        "WHERE user_id = %s AND from_admin = %s AND read_at IS NULL",
        (utcnow_iso(), user_id, True),
    )
    db.commit()
    return flask.jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Owner-only helpers -- see module docstring: the caller (server/app.py)
# must already have verified the current user is the configured owner.
# ---------------------------------------------------------------------------

def list_conversations(db):
    """One row per user who has ever sent (or received) a support message,
    each carrying only its own most recent message -- via a correlated
    subquery rather than a window function so this stays portable to
    whatever SQLite build is on the machine running the app, not just
    recent ones. Ordered unanswered-first (the user's own message is the
    most recent one in the thread, i.e. the owner hasn't replied since),
    then by recency within each group -- matching the spec's "unanswered
    questions first"."""
    rows = db.execute(
        """
        SELECT sm.user_id, u.email, u.public_name, u.display_name,
               sm.body AS last_body, sm.from_admin AS last_from_admin,
               sm.created_at AS last_created_at
        FROM support_messages sm
        JOIN users u ON u.id = sm.user_id
        WHERE sm.created_at = (
            SELECT MAX(sm2.created_at) FROM support_messages sm2
            WHERE sm2.user_id = sm.user_id
        )
        ORDER BY sm.from_admin ASC, sm.created_at DESC
        """
    ).fetchall()

    result = []
    for r in rows:
        result.append({
            "user_id": r["user_id"],
            "email": r["email"],
            "name": r["public_name"] or r["display_name"] or "",
            "last_body": r["last_body"],
            "unanswered": not bool(r["last_from_admin"]),
            "last_created_at": iso_or_none(r["last_created_at"]),
        })
    return result


def list_messages_for_user(db, user_id):
    """Full thread for one user, oldest first -- for rendering the admin
    page's expanded conversation view."""
    rows = db.execute(
        "SELECT id, body, from_admin, created_at, read_at FROM support_messages "
        "WHERE user_id = %s ORDER BY created_at ASC",
        (user_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "body": r["body"],
            "from_admin": bool(r["from_admin"]),
            "created_at": iso_or_none(r["created_at"]),
            "read_at": iso_or_none(r["read_at"]),
        }
        for r in rows
    ]


def post_admin_reply(db, user_id, body: str) -> None:
    """Writes one admin reply into user_id's conversation. Commits on its
    own, matching server/spotify_requests.py:mark_request_added's pattern
    for a single owner-triggered write."""
    db.execute(
        "INSERT INTO support_messages (user_id, body, from_admin, created_at) "
        "VALUES (%s, %s, %s, %s)",
        (user_id, body, True, utcnow_iso()),
    )
    db.commit()
