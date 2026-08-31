"""Owner-only study-time correction and audit trail.

Route registration and the owner check itself live in server/app.py (the
`/admin/users`, `/admin/users/<id>/add-time` and `/admin/actions` routes),
matching the existing pattern from server/spotify_requests.py: this module
is plain data access and business logic, called only after the caller has
already verified the current user is the configured owner. Nothing here
checks that on its own.

The core problem this solves (see SPEC's "Feature 1"): `leaderboard_weeks`
is recomputed wholesale from a user's `user_state.payload` on every
`PUT /api/state` (server/state.py -> server/leaderboard.py:upsert_week_seconds).
Writing a correction directly into `leaderboard_weeks` would therefore be
silently erased the next time that user's browser syncs. So a correction
has to become a real entry in the user's own session log instead -- see
`inject_time_correction` below -- which then flows through stats, badges
and the leaderboard exactly like a session the user logged themselves, and
survives every future sync.
"""
import datetime
import json

from .db import get_db, iso_or_none, utcnow, utcnow_iso
from .leaderboard import upsert_week_seconds
from .security import INTEGRITY_ERRORS

# ---------------------------------------------------------------------------
# Validation -- deliberately strict. A malformed session entry written into
# a user's payload breaks their app, not just this admin page, so every
# field is checked before anything touches the database.
# ---------------------------------------------------------------------------

MAX_MINUTES = 24 * 60  # spec: reject a single correction over 24 hours
MAX_COURSE_LENGTH = 100
MAX_REASON_LENGTH = 500


def validate_minutes(raw):
    """Returns (minutes:int, error:None) or (None, error:str)."""
    if isinstance(raw, bool):
        return None, "Minutes must be a whole number."
    try:
        # Accept "45" (form field) or 45 (already an int) -- reject "45.5"
        # etc. by requiring the stripped string to be a plain integer.
        text = str(raw).strip()
        if not text or not (text.isdigit() or (text.startswith("-") and text[1:].isdigit())):
            return None, "Minutes must be a whole number."
        minutes = int(text)
    except (TypeError, ValueError):
        return None, "Minutes must be a whole number."
    if minutes <= 0:
        return None, "Minutes must be a positive number."
    if minutes > MAX_MINUTES:
        return None, "A single correction can't exceed 24 hours ({} minutes).".format(MAX_MINUTES)
    return minutes, None


def validate_date(raw):
    """Returns (iso_date_str, error:None) or (None, error:str). Must parse
    and must not be in the future (compared against the server's UTC date --
    the same clock every other *_at column in this app is written in)."""
    if not isinstance(raw, str) or not raw.strip():
        return None, "Date is required."
    try:
        parsed = datetime.date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None, "Date must be a valid date (YYYY-MM-DD)."
    if parsed > utcnow().date():
        return None, "Date can't be in the future."
    return parsed.isoformat(), None


def validate_course(raw):
    if not isinstance(raw, str):
        return None, "Course is required."
    value = raw.strip()
    if not value:
        return None, "Course is required."
    if len(value) > MAX_COURSE_LENGTH:
        return None, "Course name is too long (max {} characters).".format(MAX_COURSE_LENGTH)
    return value, None


def validate_reason(raw):
    if not isinstance(raw, str):
        return None, "A reason is required."
    value = raw.strip()
    if not value:
        return None, "A reason is required."
    if len(value) > MAX_REASON_LENGTH:
        return None, "Reason is too long (max {} characters).".format(MAX_REASON_LENGTH)
    return value, None


# ---------------------------------------------------------------------------
# The correction itself
# ---------------------------------------------------------------------------

def _session_timestamp_label(now: datetime.datetime) -> str:
    """A human "3:45 PM"-style label matching what the client itself writes
    for a genuine session (index.html's saveEngineWorkspaceBlockData uses
    `Date.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})`, which
    renders without a leading zero on the hour). strftime's %I always pads
    to two digits, so strip a leading zero to match."""
    label = now.strftime("%I:%M %p")
    return label[1:] if label.startswith("0") else label


def build_session_entry(minutes: int, date_str: str, course: str, reason: str) -> dict:
    """The exact shape a genuine client-logged session carries (see
    index.html's saveEngineWorkspaceBlockData: date, course, type,
    durationSeconds, timestamp, hourOfDayExecuted), plus two extra fields
    that mark this one as an admin correction rather than tampering:
    `addedByAdmin` (bool) and `adminReason` (the owner's stated reason) --
    the frontend surfaces both so the correction is visible to the user it
    was made for, not something happening to their data invisibly.

    `type` is fixed to "Countdown" (the two genuine values are "Countdown"
    and "Stopwatch" depending on which timer mode was running -- neither is
    more "correct" for a manually-added entry, so pick the more common
    default) and `hourOfDayExecuted` uses the current server hour purely so
    the entry has *a* value in the same 0-23 range the client always writes
    (it feeds the Early Bird / Night Owl badge checks, which is a livable,
    non-harmful side effect of an admin-added session rather than something
    to special-case around).
    """
    now = utcnow()
    return {
        "date": date_str,
        "course": course,
        "type": "Countdown",
        "durationSeconds": minutes * 60,
        "timestamp": _session_timestamp_label(now),
        "hourOfDayExecuted": now.hour,
        "addedByAdmin": True,
        "adminReason": reason,
    }


MAX_CORRECTION_RACE_ATTEMPTS = 5


def inject_time_correction(db, admin_user_id: int, target_user_id: int,
                            minutes: int, date_str: str, course: str, reason: str) -> dict:
    """Writes the correction into the target user's state payload as a real
    session, bumps user_state.version (so a client holding a stale version
    hits the existing 409-conflict path in PUT /api/state and re-fetches
    instead of clobbering the correction), recomputes that user's
    leaderboard_weeks row using the exact same logic PUT /api/state uses,
    and records an admin_actions row. All in one transaction: either
    everything above lands, or (on an exception before the commit at the
    bottom) none of it does.

    Read-modify-write against `user_state`, guarded the same way
    server/state.py:put_state's own write is: the target user could be
    actively syncing (PUT /api/state) at the same moment this correction is
    added -- both operations read-then-write the same row, and without a
    version-guarded write, whichever commits second would silently overwrite
    whichever committed first (Postgres's default READ COMMITTED isolation
    does not prevent this -- see put_state's own comment for the full
    mechanics). For a normal user sync that's a 409 the client's own retry
    logic already handles; for this owner-only, comparatively rare action
    there's no client to retry, so the read-modify-write is retried here
    instead, bounded by MAX_CORRECTION_RACE_ATTEMPTS so a pathological,
    continuous stream of writes from the target user can't hang this call
    forever.

    Returns the injected session entry (as a plain dict) for the caller to
    report back, e.g. in a flash message or JSON response.
    """
    entry = build_session_entry(minutes, date_str, course, reason)

    for attempt in range(MAX_CORRECTION_RACE_ATTEMPTS):
        row = db.execute(
            "SELECT payload, version FROM user_state WHERE user_id = %s", (target_user_id,)
        ).fetchone()

        if row is None:
            payload = {}
            current_version = 0
        else:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            current_version = row["version"]

        sessions = payload.get("sessions")
        if not isinstance(sessions, list):
            sessions = []
        # unshift, matching appState.sessions.unshift(logDataNode) in
        # index.html -- newest session first, same order a genuine log entry
        # would land in.
        sessions.insert(0, entry)
        payload["sessions"] = sessions

        new_version = current_version + 1
        now_iso = utcnow_iso()
        raw = json.dumps(payload)

        try:
            if row is None:
                cursor = db.execute(
                    "INSERT INTO user_state (user_id, payload, version, updated_at) VALUES (%s, %s, %s, %s)",
                    (target_user_id, raw, new_version, now_iso),
                )
            else:
                cursor = db.execute(
                    "UPDATE user_state SET payload = %s, version = %s, updated_at = %s "
                    "WHERE user_id = %s AND version = %s",
                    (raw, new_version, now_iso, target_user_id, current_version),
                )
        except INTEGRITY_ERRORS:
            cursor = None

        if cursor is None or cursor.rowcount == 0:
            # Lost the race -- someone else's write (almost always the
            # target user's own PUT /api/state) landed in between our SELECT
            # and this write. Roll back and retry against the now-current
            # row rather than silently overwriting it.
            db.rollback()
            continue

        # Same recompute PUT /api/state performs, on the same connection/
        # transaction, so the leaderboard reflects the correction immediately
        # rather than waiting for the user's next sync.
        upsert_week_seconds(db, target_user_id, payload)

        db.execute(
            "INSERT INTO admin_actions (admin_user_id, target_user_id, action, detail, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                admin_user_id,
                target_user_id,
                "add_time",
                json.dumps({
                    "minutes": minutes,
                    "date": date_str,
                    "course": course,
                    "reason": reason,
                }),
                now_iso,
            ),
        )

        db.commit()
        return entry

    raise RuntimeError(
        "Could not save this time correction after {} attempts -- the target "
        "user's own data kept changing concurrently. Please try again.".format(
            MAX_CORRECTION_RACE_ATTEMPTS
        )
    )


# ---------------------------------------------------------------------------
# Owner-only read helpers -- callers (server/app.py's /admin/* routes) MUST
# have already verified the current user is the configured owner before
# calling any of these. See the module docstring.
# ---------------------------------------------------------------------------

def get_user_row(db, user_id):
    """A single user's public-facing identity fields -- id/email/name --
    nothing sensitive. Used both to render the target's identity on the
    admin page and to 404 an add-time POST for a user id that doesn't
    exist."""
    return db.execute(
        "SELECT id, email, public_name, display_name FROM users WHERE id = %s",
        (user_id,),
    ).fetchone()


def _display_name(row) -> str:
    return row["public_name"] or row["display_name"] or ""


def list_users_overview(db):
    """One row per user: id, email, a display name if they have one, this
    week's study seconds, and a total session count -- "the minimum needed
    to do the job" (spec). Deliberately excludes password_hash, session
    tokens, Spotify tokens, and the raw state payload itself: `state_payload`
    is read here only to derive `total_sessions` and is never included in
    the returned dict, so it never reaches a template or the browser.
    """
    from .leaderboard import current_week_start

    week_start_str = current_week_start().isoformat()
    rows = db.execute(
        """
        SELECT u.id, u.email, u.public_name, u.display_name, u.created_at,
               COALESCE(lw.seconds, 0) AS weekly_seconds,
               us.payload AS state_payload
        FROM users u
        LEFT JOIN leaderboard_weeks lw ON lw.user_id = u.id AND lw.week_start = %s
        LEFT JOIN user_state us ON us.user_id = u.id
        ORDER BY u.created_at DESC
        """,
        (week_start_str,),
    ).fetchall()

    result = []
    for r in rows:
        total_sessions = 0
        state_payload = r["state_payload"]
        if state_payload:
            try:
                parsed = json.loads(state_payload)
            except (TypeError, ValueError):
                parsed = None
            sessions = parsed.get("sessions") if isinstance(parsed, dict) else None
            if isinstance(sessions, list):
                total_sessions = len(sessions)
        result.append({
            "id": r["id"],
            "email": r["email"],
            "name": _display_name(r),
            "weekly_seconds": r["weekly_seconds"],
            "total_sessions": total_sessions,
            "created_at": iso_or_none(r["created_at"]),
        })
    return result


def list_admin_actions(db, limit: int = 200):
    """Most recent admin_actions rows first, joined to both the admin's and
    the target's own email so the audit page reads as a sentence ("owner
    added 45 minutes to user@example.com")."""
    rows = db.execute(
        """
        SELECT aa.id, aa.action, aa.detail, aa.created_at,
               au.email AS admin_email,
               tu.email AS target_email
        FROM admin_actions aa
        JOIN users au ON au.id = aa.admin_user_id
        JOIN users tu ON tu.id = aa.target_user_id
        ORDER BY aa.created_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()

    result = []
    for r in rows:
        try:
            detail = json.loads(r["detail"]) if r["detail"] else {}
        except (TypeError, ValueError):
            detail = {}
        result.append({
            "id": r["id"],
            "action": r["action"],
            "admin_email": r["admin_email"],
            "target_email": r["target_email"],
            "detail": detail,
            "created_at": iso_or_none(r["created_at"]),
        })
    return result
