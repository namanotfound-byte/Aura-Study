"""Anonymous weekly leaderboard -- blueprint 'leaderboard', prefix /api.

See SPEC-PHASE4.md "Leaderboard API" / "Anonymity rules" / "Leaderboard data".

The whole point of this module is that a response can never be traced back
to a person: an entry exposes only `rank`, `alias` and `seconds`, and the
alias is an HMAC keyed on SECRET_KEY + user id + week (never stored, never
reversible, and different every week so aliases can't be correlated across
weeks). Everything here is written with that as the load-bearing property --
read `generate_alias` and the response builders in `get_leaderboard` before
changing either.
"""
import datetime
import hashlib
import hmac
import math

import flask

from .config import get_config
from .db import get_db, utcnow, utcnow_iso
from .security import json_error, login_required, require_csrf

bp = flask.Blueprint("leaderboard", __name__)

# A week's study time is bounded above by 7*24h -- see compute_week_seconds.
MAX_WEEK_SECONDS = 7 * 24 * 60 * 60
TOP_N = 20

# Two fixed word lists (64 entries each, so a single byte can index either
# with a cheap `& 0x3F` and zero modulo bias) used to build a friendly,
# two-part alias like "Quiet Otter". Never add/remove/reorder entries in a
# way that would change which word an existing index maps to mid-week --
# doing so wouldn't leak anything (the alias is still unlinkable), but it
# would make an alias change mid-week, which is just confusing.
ADJECTIVES = [
    "Quiet", "Amber", "Brave", "Calm", "Cosmic", "Coral", "Cozy", "Crisp",
    "Curious", "Daring", "Dusty", "Eager", "Electric", "Emerald", "Fleet",
    "Fuzzy", "Gentle", "Giddy", "Golden", "Happy", "Hazy", "Humble", "Icy",
    "Indigo", "Jade", "Jolly", "Keen", "Kind", "Lively", "Lucky", "Lunar",
    "Merry", "Misty", "Mellow", "Nimble", "Noble", "Nutty", "Opal",
    "Playful", "Plucky", "Polar", "Quick", "Quirky", "Radiant", "Rosy",
    "Rustic", "Sandy", "Serene", "Shy", "Silent", "Silver", "Sly", "Snappy",
    "Solar", "Sparkly", "Spry", "Steady", "Stellar", "Sunny", "Swift",
    "Tidy", "Vivid", "Wild", "Zesty",
]
NOUNS = [
    "Otter", "Kite", "Fox", "Wren", "Heron", "Falcon", "Sparrow", "Robin",
    "Finch", "Lynx", "Panther", "Tiger", "Panda", "Koala", "Rabbit",
    "Badger", "Beaver", "Squirrel", "Hedgehog", "Puffin", "Penguin",
    "Dolphin", "Whale", "Seal", "Walrus", "Narwhal", "Turtle", "Gecko",
    "Iguana", "Chameleon", "Comet", "Meteor", "Lantern", "Compass",
    "Anchor", "Harbor", "Meadow", "Willow", "Maple", "Cedar", "Birch",
    "Canyon", "Glacier", "Summit", "Ridge", "Valley", "Prairie", "Tundra",
    "Lagoon", "Cove", "Reef", "Ember", "Spark", "Cinder", "Breeze", "Gale",
    "Frost", "Dew", "Mist", "Cloud", "Storm", "Thunder", "Horizon",
    "Zephyr",
]
assert len(ADJECTIVES) == 64 and len(NOUNS) == 64  # see the `& 0x3F` indexing below

# 32 characters, deliberately excluding 0/O/1/I (easily confused when read
# aloud or hand-copied) -- 256 % 32 == 0 so `byte % len(...)` below is
# exactly uniform, not just approximately.
SUFFIX_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
assert len(SUFFIX_ALPHABET) == 32


# --------------------------------------------------------------- week math

def week_start_for(value) -> datetime.date:
    """The Monday (UTC) of the ISO week containing `value` (a date or
    datetime)."""
    d = value.date() if isinstance(value, datetime.datetime) else value
    return d - datetime.timedelta(days=d.weekday())


def current_week_start() -> datetime.date:
    return week_start_for(utcnow())


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


# ------------------------------------------------------------------ alias

def generate_alias(user_id: int, week_start_str: str) -> str:
    """HMAC-SHA256(SECRET_KEY, "leaderboard:<user_id>:<week_start>"),
    rendered as "<Adjective> <Noun> #<3-char suffix>". Keyed on the week, so
    the same user gets an unrelated-looking alias every week -- there is no
    way to recover `user_id` from the alias, and no way to tell that two
    aliases in different weeks belong to the same account. Never persisted
    anywhere; recomputed fresh on every response.
    """
    cfg = get_config()
    msg = "leaderboard:{}:{}".format(user_id, week_start_str).encode("utf-8")
    digest = hmac.new(cfg.secret_key.encode("utf-8"), msg, hashlib.sha256).digest()
    adjective = ADJECTIVES[digest[0] & 0x3F]
    noun = NOUNS[digest[1] & 0x3F]
    suffix = "".join(SUFFIX_ALPHABET[b % len(SUFFIX_ALPHABET)] for b in digest[2:5])
    return "{} {} #{}".format(adjective, noun, suffix)


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


def upsert_week_seconds(db, user_id: int, payload) -> None:
    """Recompute and store the caller's current-week total. Called from
    server/state.py:put_state on every successful PUT /api/state, using the
    same connection/transaction as that write so the state save and the
    leaderboard recompute commit (or roll back) together.

    Does NOT touch `opted_in` on an existing row -- a returning user's
    opt-out choice for the week must survive every subsequent state sync,
    not just the first one. Defaults a first-time row to opted_in=TRUE per
    the spec ("Anonymity rules": opt-out is a deliberate action, not the
    default).
    """
    week_start = current_week_start()
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
    user_id = flask.g.user["id"]
    week_start = current_week_start()
    week_start_str = week_start.isoformat()

    my_row = db.execute(
        "SELECT seconds, opted_in FROM leaderboard_weeks WHERE user_id = %s AND week_start = %s",
        (user_id, week_start_str),
    ).fetchone()
    my_seconds = my_row["seconds"] if my_row is not None else 0
    my_opted_in = bool(my_row["opted_in"]) if my_row is not None else True

    # Only opted-in users with a nonzero total are ranked or countable as a
    # participant -- an opted-out row (or a row that exists only because the
    # user synced state with nothing logged this week) must not appear in
    # `entries` or be reflected in `participants`.
    top_rows = db.execute(
        """
        SELECT user_id, seconds FROM leaderboard_weeks
        WHERE week_start = %s AND opted_in = %s AND seconds > 0
        ORDER BY seconds DESC, user_id ASC
        LIMIT %s
        """,
        (week_start_str, True, TOP_N),
    ).fetchall()

    entries = []
    my_rank = None
    for idx, row in enumerate(top_rows, start=1):
        entries.append({
            "rank": idx,
            "alias": generate_alias(row["user_id"], week_start_str),
            "seconds": row["seconds"],
        })
        if row["user_id"] == user_id:
            my_rank = idx

    if my_opted_in and my_seconds > 0 and my_rank is None:
        # Not in the visible top N, but still has a real rank: 1 + how many
        # opted-in participants strictly outrank them.
        better = db.execute(
            """
            SELECT COUNT(*) AS c FROM leaderboard_weeks
            WHERE week_start = %s AND opted_in = %s AND seconds > %s
            """,
            (week_start_str, True, my_seconds),
        ).fetchone()
        my_rank = better["c"] + 1
    elif not my_opted_in or my_seconds <= 0:
        my_rank = None

    participants_row = db.execute(
        """
        SELECT COUNT(*) AS c FROM leaderboard_weeks
        WHERE week_start = %s AND opted_in = %s AND seconds > 0
        """,
        (week_start_str, True),
    ).fetchone()

    return flask.jsonify({
        "week_start": week_start_str,
        "you": {
            "rank": my_rank,
            "seconds": my_seconds,
            "alias": generate_alias(user_id, week_start_str),
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
    week_start_str = current_week_start().isoformat()
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
