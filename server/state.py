"""State sync API blueprint -- registered at /api by server/app.py, so the
route below resolves to /api/state. See spec section 6.

Optimistic concurrency: a client must PUT the version it last read. A stored
row starts at version 1 on first write; no row at all is treated as version 0
so a first-time PUT with version 0 (or omitted) always succeeds.
"""
import json

import flask

from .db import get_db, iso_or_none, utcnow_iso
from .leaderboard import upsert_week_seconds
from .security import INTEGRITY_ERRORS, json_error, login_required, require_csrf

bp = flask.Blueprint("state", __name__)

MAX_PAYLOAD_BYTES = 1024 * 1024


@bp.route("/state", methods=["GET"])
@login_required
def get_state():
    db = get_db()
    row = db.execute(
        "SELECT payload, version, updated_at FROM user_state WHERE user_id = %s",
        (flask.g.user["id"],),
    ).fetchone()
    if row is None:
        return flask.jsonify({"payload": None, "version": 0, "updated_at": None})
    return flask.jsonify({
        "payload": json.loads(row["payload"]),
        "version": row["version"],
        # row["updated_at"] is a datetime on Postgres (TIMESTAMPTZ) and an
        # ISO string on SQLite -- normalise so the wire format doesn't change
        # depending on backend (flask.jsonify would otherwise render a raw
        # datetime as an HTTP-date string, not ISO-8601).
        "updated_at": iso_or_none(row["updated_at"]),
    })


@bp.route("/state", methods=["PUT"])
@login_required
def put_state():
    require_csrf()
    body = flask.request.get_json(silent=True)
    if not isinstance(body, dict):
        return json_error("validation_error", "Request body must be a JSON object.", 400)

    payload = body.get("payload")
    if not isinstance(payload, dict):
        return json_error("validation_error", "payload must be a JSON object.", 400)

    client_version = body.get("version")
    if client_version is None:
        client_version = 0
    try:
        client_version = int(client_version)
    except (TypeError, ValueError):
        return json_error("validation_error", "version must be an integer.", 400)

    raw = json.dumps(payload)
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return json_error("payload_too_large", "State payload exceeds 1 MB.", 413)

    db = get_db()
    user_id = flask.g.user["id"]
    row = db.execute(
        "SELECT payload, version FROM user_state WHERE user_id = %s", (user_id,)
    ).fetchone()
    current_version = row["version"] if row is not None else 0

    if client_version != current_version:
        conflict_payload = json.loads(row["payload"]) if row is not None else None
        return flask.jsonify({
            "error": "conflict",
            "payload": conflict_payload,
            "version": current_version,
        }), 409

    new_version = current_version + 1
    now = utcnow_iso()

    # Guarded write: re-checks "the row is still at `current_version`" (or,
    # for a first-ever write, "the row still doesn't exist") at the moment
    # of the actual write, not just at the SELECT above. Without this, two
    # concurrent PUTs for the same user that both read the same
    # current_version -- a real possibility: a session log's immediate
    # flush, the regular debounce, and the pagehide/visibilitychange
    # teardown flush (static/sync.js) can all fire close together, or two
    # browser tabs push around the same moment -- would both pass the
    # equality check above, and Postgres's default READ COMMITTED isolation
    # does NOT stop the second write from silently overwriting the first's
    # already-committed row: the second transaction blocks on the row lock,
    # then proceeds once the first commits, using its own STALE
    # current_version to compute its new_version -- a classic lost update,
    # with no 409 and no error surfaced to either caller. One write simply
    # vanishes with nothing to show for it: the exact "logged but missing"
    # symptom this whole fix pass is about, just caused server-side instead
    # of client-side. Checking `cursor.rowcount` after a version-guarded
    # UPDATE (or a plain INSERT, guarded by the table's own user_id PRIMARY
    # KEY) turns that silent loss into the same 409-conflict response an
    # already-stale `client_version` gets above, with the row's actual
    # current data -- so static/sync.js's existing merge-and-retry handling
    # (see that file's handleConflict) recovers it the same way.
    try:
        if row is None:
            cursor = db.execute(
                "INSERT INTO user_state (user_id, payload, version, updated_at) VALUES (%s, %s, %s, %s)",
                (user_id, raw, new_version, now),
            )
        else:
            cursor = db.execute(
                "UPDATE user_state SET payload = %s, version = %s, updated_at = %s "
                "WHERE user_id = %s AND version = %s",
                (raw, new_version, now, user_id, current_version),
            )
    except INTEGRITY_ERRORS:
        # Two "first ever write" requests raced the INSERT itself (both saw
        # row is None) -- same outcome as rowcount == 0 below.
        cursor = None

    if cursor is None or cursor.rowcount == 0:
        db.rollback()
        fresh = db.execute(
            "SELECT payload, version FROM user_state WHERE user_id = %s", (user_id,)
        ).fetchone()
        conflict_payload = json.loads(fresh["payload"]) if fresh is not None else None
        conflict_version = fresh["version"] if fresh is not None else 0
        return flask.jsonify({
            "error": "conflict",
            "payload": conflict_payload,
            "version": conflict_version,
        }), 409

    # Recomputed wholesale from this payload's sessions on every successful
    # write (not incrementally), on the same connection/transaction as the
    # state save above -- see leaderboard.upsert_week_seconds. A failure here
    # must not silently save state while leaving the leaderboard stale, and
    # vice versa; both go out on the single db.commit() below.
    #
    # `local_date` (optional): the client's own local calendar date at sync
    # time (static/sync.js always sends it). Keeps the week this recompute
    # writes into aligned with the week the just-synced session.date strings
    # (also local dates -- see index.html's getLocalDateStr) actually fall
    # in; see leaderboard.current_week_start's docstring for why the two can
    # otherwise disagree near a week boundary.
    upsert_week_seconds(db, user_id, payload, local_date=body.get("local_date"))

    db.commit()

    return flask.jsonify({"ok": True, "version": new_version, "updated_at": now})
