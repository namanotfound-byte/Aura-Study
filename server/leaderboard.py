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
import json
import math
import re
import unicodedata

import flask

from .db import get_db, utcnow, utcnow_iso
from .security import INTEGRITY_ERRORS, json_error, login_required, require_csrf

bp = flask.Blueprint("leaderboard", __name__)

# A week's study time is bounded above by 7*24h -- see compute_week_seconds.
MAX_WEEK_SECONDS = 7 * 24 * 60 * 60
MAX_DAY_SECONDS = 24 * 60 * 60
MAX_LIFETIME_SECONDS = 10 * 365 * 24 * 60 * 60
TOP_N = 20
PET_TOP_N = 10

# Must stay in lockstep with index.html PET_LEVEL_COSTS_HOURS / PET_GARDEN_FORMS.
PET_LEVEL_COSTS_HOURS = [2, 3, 5, 8, 12, 16, 22, 30, 40, 55, 75]
PET_GARDEN_FORMS = [
    "Seedling",
    "Sprout Bun",
    "Leaf Fox",
    "Blossom Cat",
    "Grove Owl",
    "Orchard Stag",
    "Canopy Wolf",
    "Storm Cedar",
    "Mountain Grove",
    "Season Keeper",
    "World Tree",
    "Eternal Bloom",
]

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


def _session_seconds(entry) -> float:
    if not isinstance(entry, dict):
        return 0.0
    secs = entry.get("durationSeconds")
    if isinstance(secs, bool) or not isinstance(secs, (int, float)):
        return 0.0
    if not math.isfinite(secs) or secs < 0:
        return 0.0
    return float(secs)


def compute_day_seconds(payload, day: datetime.date) -> int:
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        return 0
    total = 0.0
    day_str = day.isoformat()
    for entry in sessions:
        if not isinstance(entry, dict):
            continue
        date_raw = entry.get("date")
        if not isinstance(date_raw, str) or date_raw[:10] != day_str:
            continue
        total += _session_seconds(entry)
    return min(int(total), MAX_DAY_SECONDS)


def compute_lifetime_seconds(payload) -> int:
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        return 0
    total = 0.0
    for entry in sessions:
        total += _session_seconds(entry)
    return min(int(total), MAX_LIFETIME_SECONDS)


def pet_level_cost_hours(level: int) -> int:
    if level <= len(PET_LEVEL_COSTS_HOURS):
        return PET_LEVEL_COSTS_HOURS[level - 1]
    return 100 + (level - 12) * 25


def pet_from_seconds(seconds: int):
    """Return (level, form_name) using the same garden ladder as the client."""
    total_hours = max(0, int(seconds)) / 3600.0
    level = 1
    cumulative = 0.0
    # Cap the climb so a crafted lifetime total cannot loop forever.
    for _ in range(200):
        next_cost = pet_level_cost_hours(level)
        if total_hours < cumulative + next_cost:
            break
        cumulative += next_cost
        level += 1
    form = PET_GARDEN_FORMS[min(level, 12) - 1]
    return level, form


def current_day(local_date=None) -> datetime.date:
    return _parse_client_local_date(local_date) or utcnow().date()


def upsert_day_seconds(db, user_id: int, payload, local_date=None) -> None:
    day = current_day(local_date)
    seconds = compute_day_seconds(payload, day)
    now = utcnow_iso()
    db.execute(
        """
        INSERT INTO leaderboard_days (user_id, day_date, seconds, opted_in, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (user_id, day_date) DO UPDATE SET
            seconds = excluded.seconds,
            updated_at = excluded.updated_at
        """,
        (user_id, day.isoformat(), seconds, True, now),
    )


def upsert_lifetime_seconds(db, user_id: int, payload) -> None:
    seconds = compute_lifetime_seconds(payload)
    now = utcnow_iso()
    db.execute(
        """
        INSERT INTO leaderboard_lifetime (user_id, seconds, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            seconds = excluded.seconds,
            updated_at = excluded.updated_at
        """,
        (user_id, seconds, now),
    )


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
    upsert_day_seconds(db, user_id, payload, local_date=local_date)
    upsert_lifetime_seconds(db, user_id, payload)


# ------------------------------------------------------------------ routes

def _board_from_table(db, user, table, date_col, date_value, limit=TOP_N):
    user_id = user["id"]
    my_public_name = user["public_name"]
    my_row = db.execute(
        "SELECT seconds, opted_in FROM {} WHERE user_id = %s AND {} = %s".format(table, date_col),
        (user_id, date_value),
    ).fetchone()
    my_seconds = my_row["seconds"] if my_row is not None else 0
    my_opted_in = bool(my_row["opted_in"]) if my_row is not None else True

    top_rows = db.execute(
        """
        SELECT t.user_id, t.seconds, u.public_name
        FROM {} t
        JOIN users u ON u.id = t.user_id
        WHERE t.{} = %s AND t.opted_in = %s AND t.seconds > 0
              AND u.public_name IS NOT NULL
        ORDER BY t.seconds DESC, t.user_id ASC
        LIMIT %s
        """.format(table, date_col),
        (date_value, True, limit),
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
        better = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM {} t
            JOIN users u ON u.id = t.user_id
            WHERE t.{} = %s AND t.opted_in = %s AND t.seconds > %s
                  AND u.public_name IS NOT NULL
            """.format(table, date_col),
            (date_value, True, my_seconds),
        ).fetchone()
        my_rank = better["c"] + 1
    elif not listed:
        my_rank = None

    participants_row = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM {} t
        JOIN users u ON u.id = t.user_id
        WHERE t.{} = %s AND t.opted_in = %s AND t.seconds > 0
              AND u.public_name IS NOT NULL
        """.format(table, date_col),
        (date_value, True),
    ).fetchone()

    return {
        "you": {
            "rank": my_rank,
            "seconds": my_seconds,
            "public_name": my_public_name,
            "opted_in": my_opted_in,
        },
        "entries": entries,
        "participants": participants_row["c"],
    }


@bp.route("/leaderboard", methods=["GET"])
@login_required
def get_leaderboard():
    db = get_db()
    user = flask.g.user
    local_date = flask.request.args.get("local_date")
    period = (flask.request.args.get("period") or "week").strip().lower()
    if period not in ("week", "day"):
        return json_error("validation_error", "period must be week or day.", 400)

    if period == "day":
        day_str = current_day(local_date).isoformat()
        body = _board_from_table(db, user, "leaderboard_days", "day_date", day_str)
        body["period"] = "day"
        body["day"] = day_str
        return flask.jsonify(body)

    week_start_str = current_week_start(local_date).isoformat()
    body = _board_from_table(db, user, "leaderboard_weeks", "week_start", week_start_str)
    body["period"] = "week"
    body["week_start"] = week_start_str
    return flask.jsonify(body)


def backfill_lifetime_from_user_state(db) -> None:
    """Fill leaderboard_lifetime from saved study payloads.

    The lifetime table was added after some accounts already had weeks on
    the study leaderboard. Without this, those people (e.g. Sur) stay
    invisible on the pet board until they happen to sync again.
    """
    rows = db.execute("SELECT user_id, payload FROM user_state").fetchall()
    now = utcnow_iso()
    for row in rows:
        raw = row["payload"]
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        elif isinstance(raw, dict):
            payload = raw
        else:
            continue
        seconds = compute_lifetime_seconds(payload)
        db.execute(
            """
            INSERT INTO leaderboard_lifetime (user_id, seconds, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                seconds = excluded.seconds,
                updated_at = excluded.updated_at
            """,
            (row["user_id"], seconds, now),
        )
    db.commit()


@bp.route("/leaderboard/pets", methods=["GET"])
@login_required
def get_pet_leaderboard():
    db = get_db()
    backfill_lifetime_from_user_state(db)
    user = flask.g.user
    user_id = user["id"]
    my_public_name = user["public_name"]
    week_start_str = current_week_start(flask.request.args.get("local_date")).isoformat()

    opt_row = db.execute(
        "SELECT opted_in FROM leaderboard_weeks WHERE user_id = %s AND week_start = %s",
        (user_id, week_start_str),
    ).fetchone()
    my_opted_in = bool(opt_row["opted_in"]) if opt_row is not None else True

    life = db.execute(
        "SELECT seconds FROM leaderboard_lifetime WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    my_seconds = life["seconds"] if life is not None else 0
    my_level, my_form = pet_from_seconds(my_seconds)

    top_rows = db.execute(
        """
        SELECT ll.user_id, ll.seconds, u.public_name, lw.opted_in
        FROM leaderboard_lifetime ll
        JOIN users u ON u.id = ll.user_id
        LEFT JOIN leaderboard_weeks lw
            ON lw.user_id = ll.user_id AND lw.week_start = %s
        WHERE ll.seconds > 0 AND u.public_name IS NOT NULL
              AND (lw.opted_in IS NULL OR lw.opted_in = %s)
        ORDER BY ll.seconds DESC, ll.user_id ASC
        LIMIT %s
        """,
        (week_start_str, True, PET_TOP_N),
    ).fetchall()

    entries = []
    my_rank = None
    for idx, row in enumerate(top_rows, start=1):
        level, form = pet_from_seconds(row["seconds"])
        entries.append({
            "rank": idx,
            "name": row["public_name"],
            "seconds": row["seconds"],
            "level": level,
            "form": form,
        })
        if row["user_id"] == user_id:
            my_rank = idx

    listed = my_opted_in and my_public_name is not None and my_seconds > 0
    if listed and my_rank is None:
        better = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM leaderboard_lifetime ll
            JOIN users u ON u.id = ll.user_id
            LEFT JOIN leaderboard_weeks lw
                ON lw.user_id = ll.user_id AND lw.week_start = %s
            WHERE ll.seconds > %s AND u.public_name IS NOT NULL
                  AND (lw.opted_in IS NULL OR lw.opted_in = %s)
            """,
            (week_start_str, my_seconds, True),
        ).fetchone()
        my_rank = better["c"] + 1
    elif not listed:
        my_rank = None

    return flask.jsonify({
        "you": {
            "rank": my_rank,
            "seconds": my_seconds,
            "level": my_level,
            "form": my_form,
            "public_name": my_public_name,
            "opted_in": my_opted_in,
        },
        "entries": entries,
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
    day_str = current_day(body.get("local_date")).isoformat()
    db.execute(
        """
        INSERT INTO leaderboard_days (user_id, day_date, seconds, opted_in, updated_at)
        VALUES (%s, %s, 0, %s, %s)
        ON CONFLICT (user_id, day_date) DO UPDATE SET
            opted_in = excluded.opted_in,
            updated_at = excluded.updated_at
        """,
        (user_id, day_str, opted_in, now),
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
