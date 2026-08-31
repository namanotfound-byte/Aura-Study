"""Weekly leaderboard -- blueprint 'leaderboard', prefix /api.

See the leaderboard spec: "Leaderboard API" / validation rules / "Leaderboard
data". Leaderboard entries used to show a per-week HMAC-derived alias
("Quiet Otter #A2B"); the owner asked for real, user-chosen names instead --
"Proper names should be seen on leaderboard -- not fake names." So this
module now exposes a separate, explicitly-public `users.public_name` (see
server/db.py) rather than an alias:

- `public_name` is NEVER auto-populated from `display_name` or the email --
  a user only appears on the leaderboard once they deliberately set one via
  PUT /api/leaderboard/name. No name set -> not listed, not counted in
  `participants`. Their own `you` block still reports their real rank-if-
  listed and seconds even when unlisted, so the UI can prompt them to set a
  name.
- `opted_in` (unchanged from the alias era) stays independent of having a
  name: a user can have a name set and still opt out.
- Entries still expose only `rank`, `name` and `seconds` -- never an email,
  id, display_name, join date, or anything else that could identify or
  correlate a user beyond the name they explicitly chose to publish.
"""
import datetime
import math
import re
import unicodedata

import flask

from .db import get_db, utcnow, utcnow_iso
from .security import INTEGRITY_ERRORS, json_error, login_required, require_csrf

bp = flask.Blueprint("leaderboard", __name__)

# A week's study time is bounded above by 7*24h -- see compute_week_seconds.
MAX_WEEK_SECONDS = 7 * 24 * 60 * 60
TOP_N = 20

MIN_NAME_LENGTH = 2
MAX_NAME_LENGTH = 24


# ------------------------------------------------------------ name validation
#
# This string is rendered into every other user's browser, so validation
# here is defense-in-depth on top of (never a substitute for) the frontend
# escaping it at render time -- see the module docstring and the
# <script>-payload test in tests/test_leaderboard.py.

_WHITESPACE_RUN_RE = re.compile(r"\s+")

# Reject anything that looks like an email (checked separately for "@") or a
# URL: an explicit scheme/host prefix, or a dot followed by a common TLD.
# Deliberately conservative about the TLD list -- a name with an incidental
# period (e.g. "J.R.") must not be caught by this.
_URL_LIKE_RE = re.compile(
    r"(https?://|www\.)|\.(com|net|org|io|co|edu|gov|info|biz|dev|app|xyz|"
    r"me|ai|uk|ca|de|fr|in|jp|cn|ru|tv|cc|us|ly|gg|to|so|link)\b",
    re.IGNORECASE,
)

# `<` / `>` are how HTML tags (e.g. a <script> payload) get constructed --
# rejecting them outright means a malicious name can never become markup no
# matter what the frontend later does with it. Backtick is included because
# some templating/shell contexts treat it specially; neither character has a
# legitimate use in a display name.
_DISALLOWED_CHARS_RE = re.compile(r"[<>`]")


def validate_public_name(raw):
    """Returns (cleaned_name, error_message) -- error_message is None (and
    cleaned_name is the string to store) on success."""
    if not isinstance(raw, str):
        return None, "Display name is required."

    # NFKC folds compatibility/visually-confusable Unicode forms (fullwidth
    # characters, ligatures, etc.) to a canonical form BEFORE length and
    # content checks run, so those checks see what a reader would actually
    # perceive rather than a form that could hide extra characters.
    value = unicodedata.normalize("NFKC", raw)

    # Strip control characters (category Cc -- includes \n, \r, \t and other
    # non-printables) and format characters (category Cf -- this is the
    # category zero-width spaces/joiners, bidi overrides, and the BOM all
    # fall under, so it catches every zero-width trick in one pass rather
    # than maintaining a hand-picked code point list) wherever they appear,
    # not just at the ends.
    value = "".join(ch for ch in value if unicodedata.category(ch) not in ("Cc", "Cf"))
    value = _WHITESPACE_RUN_RE.sub(" ", value).strip()

    if len(value) < MIN_NAME_LENGTH or len(value) > MAX_NAME_LENGTH:
        return None, "Display name must be between {} and {} characters.".format(
            MIN_NAME_LENGTH, MAX_NAME_LENGTH
        )

    if "@" in value or _URL_LIKE_RE.search(value):
        return None, "Display name can't contain an email address or a URL."

    if _DISALLOWED_CHARS_RE.search(value):
        return None, "Display name contains characters that aren't allowed."

    if not re.search(r"\w", value, re.UNICODE):
        return None, "Display name can't be only punctuation or whitespace."

    return value, None


# --------------------------------------------------------------- week math

def week_start_for(value) -> datetime.date:
    """The Monday (UTC) of the ISO week containing `value` (a date or
    datetime)."""
    d = value.date() if isinstance(value, datetime.datetime) else value
    return d - datetime.timedelta(days=d.weekday())


# A real IANA timezone offset never puts a client's local calendar date more
# than one day away from the server's own UTC date (offsets run from UTC-12
# to UTC+14). Bounding client-reported dates to that window is what makes
# `_parse_client_local_date` safe to trust: it can only shift which week a
# sync lands in by the same single day a genuine timezone difference would
# already cause, never further -- so a client can't steer its own total into
# an arbitrary, less-competitive week by lying about the date.
_MAX_LOCAL_DATE_SKEW_DAYS = 1


def _parse_client_local_date(raw) -> "datetime.date | None":
    """Parses an optional client-reported LOCAL calendar date (YYYY-MM-DD),
    e.g. from PUT /api/state's `local_date` body field or GET
    /api/leaderboard's `?local_date=` query param. Returns None (never
    raises) if `raw` is missing, malformed, or implausibly far from the
    server's own UTC date -- callers must fall back to the server's UTC date
    in that case, exactly as if the client hadn't sent one."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        d = datetime.date.fromisoformat(raw[:10])
    except ValueError:
        return None
    if abs((d - utcnow().date()).days) > _MAX_LOCAL_DATE_SKEW_DAYS:
        return None
    return d


def current_week_start(local_date=None) -> datetime.date:
    """The Monday of "this" week.

    Sessions are stored with the user's LOCAL calendar date (see
    index.html's getLocalDateStr -- this used to be the UTC date, which
    silently misfiled anything studied late at night, or early in the
    morning depending on the offset's sign, under the wrong day; see the
    sync/date-bug fix notes). The "current week" bucket that a state sync
    writes into (upsert_week_seconds) and the one a leaderboard read is
    served from (get_leaderboard) must agree with that same local date, not
    the server's UTC one, or a session dated by the client's local "today"
    can land outside the week window the server thinks is "current" --
    silently dropping it from the weekly total until the server's own UTC
    date catches up. `local_date`, when given (already validated by
    `_parse_client_local_date`), is that client-reported local date; falls
    back to the server's UTC date when absent or implausible.
    """
    basis = _parse_client_local_date(local_date) or utcnow().date()
    return week_start_for(basis)


def _date_str(value) -> str:
    """Normalise a week_start value read back from the DB: Postgres hands
    back a real `datetime.date` for the DATE column, SQLite hands back the
    ISO string that was written. Mirrors db.iso_or_none's role for *_at
    columns."""
    if isinstance(value, datetime.datetime):
        value = value.date()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value


# ------------------------------------------------------------ weekly total

def compute_week_seconds(payload, week_start: datetime.date) -> int:
    """Sum `durationSeconds` across `payload["sessions"]` entries whose
    `date` falls within the week starting `week_start` (Monday, through the
    following Sunday inclusive, UTC).

    `payload` is the user-controlled appState JSON blob (server/state.py
    only validates it's a JSON object under 1MB; it never otherwise
    interprets it) -- so this is deliberately paranoid: anything that
    doesn't look exactly like a well-formed session entry is skipped rather
    than raising, and the total is clamped to MAX_WEEK_SECONDS (7*24h) so no
    crafted payload -- one huge duration, thousands of fake sessions, a
    NaN/Infinity slipped into a JSON body (Python's json module accepts
    those by default) -- can fake a leaderboard position or blow up a
    downstream calculation.
    """
    week_end = week_start + datetime.timedelta(days=7)  # exclusive
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        return 0

    total = 0.0
    for entry in sessions:
        if not isinstance(entry, dict):
            continue

        date_raw = entry.get("date")
        if not isinstance(date_raw, str):
            continue
        try:
            entry_date = datetime.date.fromisoformat(date_raw[:10])
        except ValueError:
            continue
        if not (week_start <= entry_date < week_end):
            continue

        secs = entry.get("durationSeconds")
        # bool is a subclass of int in Python -- exclude it explicitly, and
        # reject NaN/+-Infinity (valid JSON as this codebase parses it, but
        # not a sane duration: `secs < 0` is always False for NaN, and
        # int(nan) raises).
        if isinstance(secs, bool) or not isinstance(secs, (int, float)):
            continue
        if not math.isfinite(secs) or secs < 0:
            continue

        total += secs

    return min(int(total), MAX_WEEK_SECONDS)


def upsert_week_seconds(db, user_id: int, payload, local_date=None) -> None:
    """Recompute and store the caller's current-week total. Called from
    server/state.py:put_state on every successful PUT /api/state, using the
    same connection/transaction as that write so the state save and the
    leaderboard recompute commit (or roll back) together.

    `local_date`: the client's reported local calendar date (see
    current_week_start), so the week this total is written into agrees with
    the week the just-synced session.date strings actually fall in.

    Does NOT touch `opted_in` on an existing row -- a returning user's
    opt-out choice for the week must survive every subsequent state sync,
    not just the first one. Defaults a first-time row to opted_in=TRUE per
    the spec ("Anonymity rules": opt-out is a deliberate action, not the
    default).
    """
    week_start = current_week_start(local_date)
    seconds = compute_week_seconds(payload, week_start)
    now = utcnow_iso()
    db.execute(
        """
        INSERT INTO leaderboard_weeks (user_id, week_start, seconds, opted_in, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (user_id, week_start) DO UPDATE SET
            seconds = excluded.seconds,
            updated_at = excluded.updated_at
        """,
        (user_id, week_start.isoformat(), seconds, True, now),
    )


# ------------------------------------------------------------------ routes

@bp.route("/leaderboard", methods=["GET"])
@login_required
def get_leaderboard():
    db = get_db()
    user = flask.g.user
    user_id = user["id"]
    my_public_name = user["public_name"]
    # See current_week_start's docstring: `local_date` (the client's own
    # local "today") keeps this read landing on the same week the client's
    # own state syncs are writing into.
    week_start = current_week_start(flask.request.args.get("local_date"))
    week_start_str = week_start.isoformat()

    my_row = db.execute(
        "SELECT seconds, opted_in FROM leaderboard_weeks WHERE user_id = %s AND week_start = %s",
        (user_id, week_start_str),
    ).fetchone()
    my_seconds = my_row["seconds"] if my_row is not None else 0
    my_opted_in = bool(my_row["opted_in"]) if my_row is not None else True

    # Only opted-in users who have set a public name AND have a nonzero
    # total are ranked, listed in `entries`, or countable as a participant --
    # no name set means "hasn't opted into being publicly identifiable yet",
    # which must behave the same as not being in `entries` at all.
    top_rows = db.execute(
        """
        SELECT lw.user_id, lw.seconds, u.public_name
        FROM leaderboard_weeks lw
        JOIN users u ON u.id = lw.user_id
        WHERE lw.week_start = %s AND lw.opted_in = %s AND lw.seconds > 0
              AND u.public_name IS NOT NULL
        ORDER BY lw.seconds DESC, lw.user_id ASC
        LIMIT %s
        """,
        (week_start_str, True, TOP_N),
    ).fetchall()

    entries = []
    my_rank = None
    for idx, row in enumerate(top_rows, start=1):
        entries.append({
            "rank": idx,
            "name": row["public_name"],
            "seconds": row["seconds"],
        })
        if row["user_id"] == user_id:
            my_rank = idx

    listed = my_opted_in and my_public_name is not None and my_seconds > 0
    if listed and my_rank is None:
        # Not in the visible top N, but still has a real rank: 1 + how many
        # listed participants strictly outrank them.
        better = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM leaderboard_weeks lw
            JOIN users u ON u.id = lw.user_id
            WHERE lw.week_start = %s AND lw.opted_in = %s AND lw.seconds > %s
                  AND u.public_name IS NOT NULL
            """,
            (week_start_str, True, my_seconds),
        ).fetchone()
        my_rank = better["c"] + 1
    elif not listed:
        my_rank = None

    participants_row = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM leaderboard_weeks lw
        JOIN users u ON u.id = lw.user_id
        WHERE lw.week_start = %s AND lw.opted_in = %s AND lw.seconds > 0
              AND u.public_name IS NOT NULL
        """,
        (week_start_str, True),
    ).fetchone()

    return flask.jsonify({
        "week_start": week_start_str,
        "you": {
            "rank": my_rank,
            "seconds": my_seconds,
            "public_name": my_public_name,
            "opted_in": my_opted_in,
        },
        "entries": entries,
        "participants": participants_row["c"],
    })


@bp.route("/leaderboard/opt", methods=["PUT"])
@login_required
def put_opt():
    require_csrf()
    body = flask.request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("opted_in"), bool):
        return json_error("validation_error", "opted_in must be a boolean.", 400)

    opted_in = body["opted_in"]
    user_id = flask.g.user["id"]
    week_start_str = current_week_start(body.get("local_date")).isoformat()
    now = utcnow_iso()

    db = get_db()
    # `seconds` in the VALUES list only takes effect if this is the first
    # row for this user+week (no state sync yet this week); an existing
    # row's seconds are left untouched by the DO UPDATE below, same
    # rationale as upsert_week_seconds not touching opted_in.
    db.execute(
        """
        INSERT INTO leaderboard_weeks (user_id, week_start, seconds, opted_in, updated_at)
        VALUES (%s, %s, 0, %s, %s)
        ON CONFLICT (user_id, week_start) DO UPDATE SET
            opted_in = excluded.opted_in,
            updated_at = excluded.updated_at
        """,
        (user_id, week_start_str, opted_in, now),
    )
    db.commit()
    return flask.jsonify({"ok": True})


@bp.route("/leaderboard/name", methods=["PUT"])
@login_required
def put_name():
    require_csrf()
    body = flask.request.get_json(silent=True)
    if not isinstance(body, dict):
        return json_error("validation_error", "Request body must be a JSON object.", 400)

    cleaned, err = validate_public_name(body.get("public_name"))
    if err:
        return json_error("validation_error", err, 400)

    db = get_db()
    user_id = flask.g.user["id"]

    # Case-insensitive uniqueness check up front (fast, clear error message
    # for the common case); the unique index on lower(public_name) in
    # server/db.py is the actual backstop against a concurrent race between
    # two users claiming the same name at once -- caught below.
    existing = db.execute(
        "SELECT id FROM users WHERE lower(public_name) = lower(%s) AND id != %s",
        (cleaned, user_id),
    ).fetchone()
    if existing is not None:
        return json_error("name_taken", "That name is already taken.", 409)

    try:
        db.execute("UPDATE users SET public_name = %s WHERE id = %s", (cleaned, user_id))
        db.commit()
    except INTEGRITY_ERRORS:
        db.rollback()
        return json_error("name_taken", "That name is already taken.", 409)

    return flask.jsonify({"ok": True, "public_name": cleaned})
