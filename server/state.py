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
from .security import json_error, login_required, require_csrf

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
    if row is None:
        db.execute(
            "INSERT INTO user_state (user_id, payload, version, updated_at) VALUES (%s, %s, %s, %s)",
            (user_id, raw, new_version, now),
        )
    else:
        db.execute(
            "UPDATE user_state SET payload = %s, version = %s, updated_at = %s WHERE user_id = %s",
            (raw, new_version, now, user_id),
        )

    # Recomputed wholesale from this payload's sessions on every successful
    # write (not incrementally), on the same connection/transaction as the
    # state save above -- see leaderboard.upsert_week_seconds. A failure here
    # must not silently save state while leaving the leaderboard stale, and
    # vice versa; both go out on the single db.commit() below.
    upsert_week_seconds(db, user_id, payload)

    db.commit()

    return flask.jsonify({"ok": True, "version": new_version, "updated_at": now})
