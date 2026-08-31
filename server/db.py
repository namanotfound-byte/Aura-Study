"""Database connection management and schema for AuraStudy.

Two backends are supported:

- **PostgreSQL** (via `psycopg` v3, a plain `psycopg.connect()` opened per
  request) when the `DATABASE_URL` env var is set. This is the production
  path (Neon).
- **SQLite** (stdlib `sqlite3`) as a zero-config local-dev fallback when
  `DATABASE_URL` is unset, so `./run.sh` keeps working with no external
  database to stand up.

There is deliberately no application-side connection pool. `DATABASE_URL` in
production points at Neon's `-pooler` endpoint, which is PgBouncer running
in front of the database -- a pool already sits between this app and
Postgres, on Neon's side. Running an app-level `psycopg_pool.ConnectionPool`
in front of that was a pool-in-front-of-a-pool, and it was actively
dangerous: Neon's free tier drops idle connections, and the pool did not
reliably reclaim the slot when that happened. Measured against the live
database, probing after idle gaps:

    t=0      pool_size=1  available=1  connections_lost=0
    t=6min   pool_size=2  available=2  connections_lost=1
    t=12min  pool_size=3  available=2  connections_lost=3   <- a slot leaked

`pool_size` climbed while `available` lagged behind it. Once `pool_size`
reached `max_size` with nothing available, every `getconn()` blocked for the
full timeout and every DB-backed request 500'd until the process was
restarted -- in production this showed up as healthy right after a deploy,
fine under sustained traffic, and dead after any idle period, logging
`PoolTimeout: couldn't get a connection after 45.00 sec` with no pool
activity otherwise. A plain connection per request sidesteps the whole
failure class: there is no pool state to leak, so there is nothing to leak.
The app and database are in the same region (Singapore), so opening a fresh
connection per request is cheap -- see the latency numbers this change was
verified with. If a future change points `DATABASE_URL` at Neon's *direct*
(non-pooled) endpoint instead, or moves to a region where connection setup
is no longer cheap, an app-side pool may be worth reconsidering then -- but
do not reintroduce one in front of the pooler endpoint without re-measuring
the leak above.

Every query in this codebase is written once, using `%s` placeholders (the
psycopg / production style -- see spec PART A: "audit every single query").
The SQLite adapter transparently rewrites `%s` -> `?` before executing, so
call sites never branch on which backend is active. Row access is mapping
-style (`row["email"]`) on both backends: `psycopg.rows.dict_row` for
Postgres, `sqlite3.Row` for SQLite.

Timestamp/boolean note: on Postgres, `TIMESTAMPTZ` columns come back as
timezone-aware `datetime.datetime` objects and `BOOLEAN` columns come back as
real `bool`s -- not the ISO-8601 strings / 0-1 ints SQLite hands back. Use
`parse_iso()` (accepts either) and `iso_or_none()` (normalises either back to
an ISO string for JSON responses) at every call site that touches a `*_at`
column. See server/auth.py:_public_user and server/state.py:get_state for
the two places that serialise a raw `*_at` value straight into an API
response.
"""
import datetime
import logging
import re
import sqlite3

import flask

from .config import get_config

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

# SQLite fallback: identical to the original single-backend schema (spec
# SPEC.md section 4) -- AUTOINCREMENT ids, TEXT timestamps, INTEGER booleans,
# COLLATE NOCASE for case-insensitive email.
SQLITE_SCHEMA = """
-- public_name is deliberately a separate column from display_name:
-- display_name is collected at registration with
-- no indication it would ever be shown to strangers, so it is never
-- auto-populated here. A user only appears on the leaderboard once they set
-- this explicitly via PUT /api/leaderboard/name -- see server/leaderboard.py.
-- NULL by default, including for every row that existed before this column
-- was added (see the ALTER TABLE migration in init_db() below).
CREATE TABLE IF NOT EXISTS users (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  email             TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash     TEXT NOT NULL,
  display_name      TEXT,
  public_name       TEXT,
  is_verified       INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  last_login_at     TEXT
);
-- Case-insensitive uniqueness so nobody can impersonate another user's
-- public name; expression index on lower(public_name) so it matches exactly
-- the comparison server/leaderboard.py's own uniqueness check uses (rather
-- than SQLite's separate, ASCII-only COLLATE NOCASE mechanism, which could
-- disagree with lower() on non-ASCII input). Partial (WHERE public_name IS
-- NOT NULL) so the column can stay NULL for every user who hasn't opted into
-- a public name -- a plain UNIQUE index already permits unlimited NULLs
-- (NULL is never equal to NULL), so this is about not indexing rows that
-- don't need it and mirroring the Postgres index below.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_public_name_lower
  ON users (lower(public_name)) WHERE public_name IS NOT NULL;

CREATE TABLE IF NOT EXISTS sessions (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash        TEXT NOT NULL UNIQUE,
  created_at        TEXT NOT NULL,
  expires_at        TEXT NOT NULL,
  user_agent        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS email_tokens (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash        TEXT NOT NULL UNIQUE,
  purpose           TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  expires_at        TEXT NOT NULL,
  used_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_email_tokens_user ON email_tokens(user_id);

CREATE TABLE IF NOT EXISTS user_state (
  user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  payload           TEXT NOT NULL,
  version           INTEGER NOT NULL DEFAULT 1,
  updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spotify_accounts (
  user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  spotify_user_id   TEXT,
  display_name      TEXT,
  product           TEXT,
  access_token      TEXT,
  refresh_token     TEXT,
  expires_at        TEXT,
  scopes            TEXT,
  connected_at      TEXT NOT NULL
);

-- One row per user: the Spotify account email they've submitted so the app
-- owner can add it by hand to the Spotify app's Development Mode User
-- Management allowlist (server/spotify_requests.py). UNIQUE user_id backs
-- the ON CONFLICT(user_id) upsert a re-submission does -- "one request per
-- user" is enforced here, not just in application logic.
CREATE TABLE IF NOT EXISTS spotify_access_requests (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id           INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  spotify_email     TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'pending',
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_attempts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  key               TEXT NOT NULL,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_key ON auth_attempts(key, created_at);

-- Weekly totals feeding the anonymous leaderboard (SPEC-PHASE4.md). One row
-- per user per ISO week (Monday 00:00 UTC), recomputed wholesale on every
-- PUT /api/state -- see server/leaderboard.py:upsert_week_seconds. week_start
-- is stored as plain ISO-date TEXT here (SQLite has no DATE type); Postgres
-- uses a real DATE column below.
CREATE TABLE IF NOT EXISTS leaderboard_weeks (
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week_start        TEXT NOT NULL,
  seconds           INTEGER NOT NULL DEFAULT 0,
  opted_in          INTEGER NOT NULL DEFAULT 1,
  updated_at        TEXT NOT NULL,
  PRIMARY KEY (user_id, week_start)
);
CREATE INDEX IF NOT EXISTS idx_leaderboard_week ON leaderboard_weeks(week_start, seconds DESC);

-- Owner-only audit trail for admin.py's study-time corrections (see
-- server/admin.py:inject_time_correction). One row per correction: who did
-- it, whose data it touched, and the full detail (minutes/date/course/
-- reason) as a JSON blob -- mirrors user_state.payload's TEXT-column-of-JSON
-- pattern rather than adding more typed columns, since `detail` is
-- action-specific and only ever read back for display, never queried on.
-- Generic enough to record other admin action *types* later, though only
-- "add_time" is written today.
CREATE TABLE IF NOT EXISTS admin_actions (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  admin_user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  action            TEXT NOT NULL,
  detail            TEXT NOT NULL,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_actions_target ON admin_actions(target_user_id);
CREATE INDEX IF NOT EXISTS idx_admin_actions_created ON admin_actions(created_at DESC);

-- Doubts/support inbox (server/support.py). One conversation per user --
-- no threading, no subjects -- so a single flat table ordered by
-- created_at is the whole schema. `from_admin` distinguishes the user's
-- own messages from the owner's replies; `read_at` tracks only the user's
-- side (has this user seen this admin reply yet) -- there is deliberately
-- no equivalent for the admin side, since the admin's own /admin/support
-- page always shows the full thread rather than an unread count.
CREATE TABLE IF NOT EXISTS support_messages (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body              TEXT NOT NULL,
  from_admin        INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  read_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_support_messages_user ON support_messages(user_id, created_at);
"""

# Postgres (production, Neon): IDENTITY primary keys, TIMESTAMPTZ throughout,
# real BOOLEAN, and a case-insensitive unique index on email (the app also
# lowercases every email before it touches the database -- see auth.py -- so
# this index is defence-in-depth, not the only thing enforcing it).
# public_name is a separate, deliberately-opt-in column from display_name --
# see the matching comment on SQLITE_SCHEMA's users table above for why it
# is never auto-populated. NULL by default, including for every row that
# existed before this column was added (see the ALTER TABLE migration in
# init_db() below).
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email             TEXT NOT NULL,
  password_hash     TEXT NOT NULL,
  display_name      TEXT,
  public_name       TEXT,
  is_verified       BOOLEAN NOT NULL DEFAULT FALSE,
  created_at        TIMESTAMPTZ NOT NULL,
  last_login_at     TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON users (lower(email));
-- Case-insensitive uniqueness on the public leaderboard name -- see the
-- matching index on SQLITE_SCHEMA above for the full rationale. Partial
-- (WHERE public_name IS NOT NULL) so users who haven't set one don't
-- collide on NULL.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_public_name_lower
  ON users (lower(public_name)) WHERE public_name IS NOT NULL;

CREATE TABLE IF NOT EXISTS sessions (
  id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash        TEXT NOT NULL UNIQUE,
  created_at        TIMESTAMPTZ NOT NULL,
  expires_at        TIMESTAMPTZ NOT NULL,
  user_agent        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS email_tokens (
  id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash        TEXT NOT NULL UNIQUE,
  purpose           TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL,
  expires_at        TIMESTAMPTZ NOT NULL,
  used_at           TIMESTAMPTZ
);
-- token_hash already has a unique index via the UNIQUE constraint above; the
-- lookup that was actually missing an index is "invalidate previous unused
-- tokens for this user+purpose" (server/auth.py:_issue_email_token).
CREATE INDEX IF NOT EXISTS idx_email_tokens_user ON email_tokens(user_id);

CREATE TABLE IF NOT EXISTS user_state (
  user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  payload           TEXT NOT NULL,
  version           INTEGER NOT NULL DEFAULT 1,
  updated_at        TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS spotify_accounts (
  user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  spotify_user_id   TEXT,
  display_name      TEXT,
  product           TEXT,
  access_token      TEXT,
  refresh_token     TEXT,
  expires_at        TIMESTAMPTZ,
  scopes            TEXT,
  connected_at      TIMESTAMPTZ NOT NULL
);

-- One row per user: the Spotify account email they've submitted so the app
-- owner can add it by hand to the Spotify app's Development Mode User
-- Management allowlist (server/spotify_requests.py). UNIQUE user_id backs
-- the ON CONFLICT(user_id) upsert a re-submission does -- "one request per
-- user" is enforced here, not just in application logic.
CREATE TABLE IF NOT EXISTS spotify_access_requests (
  id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id           INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  spotify_email     TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'pending',
  created_at        TIMESTAMPTZ NOT NULL,
  updated_at        TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_attempts (
  id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key               TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_key ON auth_attempts(key, created_at);

-- Weekly totals feeding the anonymous leaderboard (SPEC-PHASE4.md). One row
-- per user per ISO week (Monday 00:00 UTC), recomputed wholesale on every
-- PUT /api/state -- see server/leaderboard.py:upsert_week_seconds.
CREATE TABLE IF NOT EXISTS leaderboard_weeks (
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week_start        DATE NOT NULL,
  seconds           INTEGER NOT NULL DEFAULT 0,
  opted_in          BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at        TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (user_id, week_start)
);
CREATE INDEX IF NOT EXISTS idx_leaderboard_week ON leaderboard_weeks(week_start, seconds DESC);

-- See the matching comment on SQLITE_SCHEMA's admin_actions above -- same
-- shape, TIMESTAMPTZ instead of TEXT for created_at.
CREATE TABLE IF NOT EXISTS admin_actions (
  id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  admin_user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  action            TEXT NOT NULL,
  detail            TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_actions_target ON admin_actions(target_user_id);
CREATE INDEX IF NOT EXISTS idx_admin_actions_created ON admin_actions(created_at DESC);

-- See the matching comment on SQLITE_SCHEMA's support_messages above -- same
-- shape, TIMESTAMPTZ instead of TEXT and a real BOOLEAN for from_admin.
CREATE TABLE IF NOT EXISTS support_messages (
  id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body              TEXT NOT NULL,
  from_admin        BOOLEAN NOT NULL DEFAULT FALSE,
  created_at        TIMESTAMPTZ NOT NULL,
  read_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_support_messages_user ON support_messages(user_id, created_at);
"""


# ---------------------------------------------------------------------------
# Time helpers (frozen contract -- server/security.py, server/auth.py,
# server/spotify.py, server/app.py, and the test suite all import these)
# ---------------------------------------------------------------------------

def utcnow() -> datetime.datetime:
    """Timezone-aware current UTC time."""
    return datetime.datetime.now(datetime.timezone.utc)


def utcnow_iso() -> str:
    """ISO-8601 string. Still used for values that don't round-trip through
    a TIMESTAMPTZ column read (e.g. building a fresh JSON response), and it
    is exactly what gets bound into `*_at` columns on write -- Postgres casts
    an ISO-8601 text parameter to `timestamptz` automatically, so this is
    safe to pass straight into a parameterised INSERT/UPDATE on both
    backends."""
    return utcnow().isoformat()


def parse_iso(value) -> datetime.datetime:
    """Parse a stored `*_at` value back into a timezone-aware datetime.

    On SQLite, `*_at` columns are TEXT and come back as the ISO-8601 string
    `utcnow_iso()` wrote. On Postgres, TIMESTAMPTZ columns come back from
    psycopg as an already timezone-aware `datetime.datetime` -- so this
    accepts both and normalises to a tz-aware UTC-equivalent datetime,
    instead of assuming a string as the original single-backend version did.
    """
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def iso_or_none(value):
    """Normalise a raw `*_at` column value for a JSON response.

    Postgres hands back a `datetime.datetime` for TIMESTAMPTZ columns, which
    `flask.jsonify` does NOT render as ISO-8601 (it uses HTTP-date format),
    silently changing the API's wire format. SQLite hands back the ISO-8601
    string already. This makes both backends produce the same JSON shape.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


# ---------------------------------------------------------------------------
# SQLite adapter -- rewrites %s -> ? so every query can be written once
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"%s")


class _SqliteConnAdapter:
    """Thin wrapper around sqlite3.Connection matching the subset of the
    psycopg Connection interface this codebase uses (`execute`, `commit`,
    `rollback`, `close`), and translating our %s-style queries to sqlite's
    `?` style so call sites don't need to know which backend is active."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(_PLACEHOLDER_RE.sub("?", sql), params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _sqlite_connect(database_path: str) -> _SqliteConnAdapter:
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return _SqliteConnAdapter(conn)


# ---------------------------------------------------------------------------
# Postgres adapter -- one connection per request, no app-side pool
# ---------------------------------------------------------------------------
#
# See the module docstring for why: Neon's `-pooler` endpoint is already
# PgBouncer, an app-side pool stacked in front of it leaked slots whenever
# Neon's free tier dropped an idle connection, and in-region connection
# setup is cheap enough that opening one per request is not worth the risk
# of reintroducing that failure class.


def _pg_connect(database_url: str):
    from psycopg.rows import dict_row
    import psycopg

    return psycopg.connect(
        database_url,
        # Fail fast rather than hang the whole gunicorn request timeout if
        # the network path to Neon is broken. A cold Neon compute resume
        # was measured at up to ~36s in production (server/db.py history),
        # so this is generous enough to ride that out while still bounded.
        connect_timeout=30,
        row_factory=dict_row,
        autocommit=False,
        # Force the session timezone to UTC. Postgres always stores
        # TIMESTAMPTZ internally as UTC, but renders it back to Python (and
        # would render it in SQL text) in whatever the session's TimeZone
        # setting is -- without this, a datetime read back from the same
        # row as it was written could show a non-UTC offset (e.g. the host
        # machine's local zone), which is confusing even though it's the
        # same instant. Keeps the "timezone-aware UTC throughout" rule
        # visibly true, not just technically true.
        options="-c TimeZone=UTC",
        # Disable psycopg3's automatic server-side prepared statements
        # (default: PREPARE after the same query text has run 5 times on a
        # connection -- `prepare_threshold`). `DATABASE_URL` in production
        # points at Neon's pooled (`-pooler`, PgBouncer-style
        # transaction-mode) endpoint. Transaction-mode poolers are free to
        # serve a client's next transaction from a *different* backend
        # server process than the one that served its last -- a
        # server-side PREPARE issued on backend A is simply not there when
        # psycopg later sends EXECUTE for it against backend B, raising
        # "prepared statement ... does not exist". This is still required
        # with per-request connections: each request is exactly the kind of
        # short-lived client PgBouncer is free to bounce between backends,
        # so the same statement text seen 5 times *within one connection's
        # lifetime* (e.g. a request that queries the same table twice) can
        # still trip it. Setting prepare_threshold=None makes every query a
        # plain (unprepared) parameterised EXECUTE -- still fully
        # parameterised/SQL-injection-safe, just without the
        # server-side-PREPARE optimisation.
        prepare_threshold=None,
    )


class _PgConnAdapter:
    """Wraps a plain (non-pooled) psycopg connection to match the same
    `execute` / `commit` / `rollback` / `close` surface as
    `_SqliteConnAdapter`. `close` actually closes the underlying socket --
    there is no pool to return the connection to."""

    __slots__ = ("_conn",)

    def __init__(self, database_url: str):
        self._conn = _pg_connect(database_url)

    def execute(self, sql, params=()):
        return self._conn.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Public, backend-agnostic API (frozen contract -- see SPEC.md section 13)
# ---------------------------------------------------------------------------

def _using_postgres(cfg) -> bool:
    return bool(getattr(cfg, "database_url", ""))


def get_db():
    """Return the per-request connection, creating it on first use.

    Postgres when `DATABASE_URL` is configured (a fresh `psycopg.connect()`,
    closed at teardown -- see the module docstring for why there is no
    app-side pool); SQLite otherwise (a plain file connection, also closed
    at teardown). Both expose the same `execute`/`commit` surface and both
    return mapping-style rows (`row["col"]`).
    """
    if "db" not in flask.g:
        cfg = get_config()
        if _using_postgres(cfg):
            try:
                flask.g.db = _PgConnAdapter(cfg.database_url)
            except Exception as exc:
                # A failed connection attempt gives no hint on its own
                # whether the cause was DNS, routing, TLS, a rejected
                # password, or Neon's compute still waking up -- log the
                # exception type and message (never the connection string
                # or anything derived from it, which could contain the
                # password) before re-raising, so the real reason ends up
                # in the logs instead of just "the request 500'd".
                logging.getLogger(__name__).error(
                    "DB DIAGNOSTIC: connection to Postgres failed -- %s: %s",
                    type(exc).__name__,
                    exc,
                )
                raise
        else:
            flask.g.db = _sqlite_connect(cfg.database_path)
    return flask.g.db


def close_db(exception=None):
    conn = flask.g.pop("db", None)
    if conn is not None:
        if exception is not None:
            # Best-effort: an unhandled error mid-request must not leave a
            # half-committed transaction on a connection. There's no pool to
            # reset it for us now that each request gets its own connection
            # (previously psycopg_pool did this on return), so roll back
            # explicitly before closing.
            try:
                conn.rollback()
            except Exception:
                pass
        conn.close()


# ---------------------------------------------------------------------------
# Migration: users.public_name
# ---------------------------------------------------------------------------
#
# `CREATE TABLE IF NOT EXISTS` (in PG_SCHEMA/SQLITE_SCHEMA above) is a no-op
# on a `users` table that already exists -- which is exactly the case in
# production, where a real row predates this column. Without an explicit
# `ALTER TABLE`, that row (and the table itself) would simply never gain
# `public_name`, and every later `SELECT * FROM users` / `flask.g.user`
# lookup would KeyError on it. Both helpers below run BEFORE the schema
# script and are no-ops in the two cases that don't need them: a brand-new
# database (the table doesn't exist yet -- the schema script below creates it
# with `public_name` already present) and a database that already has the
# column (a previous boot already migrated it). An existing row's other
# columns are untouched; `public_name` simply reads back as NULL, which is
# exactly "hasn't set a public name yet" -- server/leaderboard.py treats that
# as "not listed", not an error.

def _pg_migrate_public_name(conn) -> None:
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'users'"
    ).fetchone()
    if exists is None:
        return  # fresh database; PG_SCHEMA below creates the column directly
    conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS public_name TEXT")
    conn.commit()


def _sqlite_migrate_public_name(conn: sqlite3.Connection) -> None:
    # PRAGMA table_info + a plain ALTER TABLE, rather than relying on
    # SQLite's own `ADD COLUMN IF NOT EXISTS` syntax (only available since
    # 3.35.0 / 2021-03) -- this works on any SQLite version.
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    if exists is None:
        return  # fresh database; SQLITE_SCHEMA below creates the column directly
    cols = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "public_name" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN public_name TEXT")
        conn.commit()


def init_db(app: flask.Flask) -> None:
    """Create the schema (idempotent) and register the per-request
    teardown. Safe to call on every boot."""
    cfg = get_config()
    if _using_postgres(cfg):
        conn = _pg_connect(cfg.database_url)
        try:
            _pg_migrate_public_name(conn)
            conn.execute(PG_SCHEMA)
            conn.commit()
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(cfg.database_path)
        try:
            _sqlite_migrate_public_name(conn)
            conn.executescript(SQLITE_SCHEMA)
            conn.commit()
        finally:
            conn.close()
    app.teardown_appcontext(close_db)
