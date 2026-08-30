"""Database connection management and schema for AuraStudy.

Two backends are supported:

- **PostgreSQL** (via `psycopg` v3 + a `psycopg_pool.ConnectionPool`) when the
  `DATABASE_URL` env var is set. This is the production path (Neon).
- **SQLite** (stdlib `sqlite3`) as a zero-config local-dev fallback when
  `DATABASE_URL` is unset, so `./run.sh` keeps working with no external
  database to stand up.

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
import threading

import flask

from .config import get_config

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

# SQLite fallback: identical to the original single-backend schema (spec
# SPEC.md section 4) -- AUTOINCREMENT ids, TEXT timestamps, INTEGER booleans,
# COLLATE NOCASE for case-insensitive email.
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  email             TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash     TEXT NOT NULL,
  display_name      TEXT,
  is_verified       INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  last_login_at     TEXT
);

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

CREATE TABLE IF NOT EXISTS auth_attempts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  key               TEXT NOT NULL,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_key ON auth_attempts(key, created_at);
"""

# Postgres (production, Neon): IDENTITY primary keys, TIMESTAMPTZ throughout,
# real BOOLEAN, and a case-insensitive unique index on email (the app also
# lowercases every email before it touches the database -- see auth.py -- so
# this index is defence-in-depth, not the only thing enforcing it).
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email             TEXT NOT NULL,
  password_hash     TEXT NOT NULL,
  display_name      TEXT,
  is_verified       BOOLEAN NOT NULL DEFAULT FALSE,
  created_at        TIMESTAMPTZ NOT NULL,
  last_login_at     TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON users (lower(email));

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

CREATE TABLE IF NOT EXISTS auth_attempts (
  id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key               TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_key ON auth_attempts(key, created_at);
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
# Postgres adapter -- pooled connections
# ---------------------------------------------------------------------------

# One pool per DATABASE_URL, process-wide (keyed rather than a single global
# so the test suite -- which spins up a fresh Postgres database per test --
# can hold several pools alive at once and tear each down independently).
_pg_pools = {}
_pg_pools_lock = threading.Lock()


def _get_pg_pool(database_url: str):
    with _pg_pools_lock:
        pool = _pg_pools.get(database_url)
        if pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            # Sized for Render's free web tier: small and bounded. Neon also
            # offers a separate pooled (pgbouncer) endpoint for serverless /
            # high-connection-count clients; either DATABASE_URL works here,
            # but prefer the *direct* (non-pooled) Neon connection string
            # when running behind this app-level pool, and the *pooled*
            # Neon endpoint only if this pool is bypassed (e.g. a one-off
            # script). Using both stacked is fine but redundant.
            pool = ConnectionPool(
                conninfo=database_url,
                # Neon's free tier suspends the compute after a few minutes
                # idle, but Render pings /healthz every 5s, so the web process
                # stays alive holding connections to a database that went to
                # sleep underneath it. Without the settings below the pool
                # hands out those dead connections forever and every
                # DB-backed request hangs until the 30s default timeout --
                # observed in production: the app 500'd on every DB route
                # while /healthz stayed green, and only a redeploy cleared it.
                #
                # min_size=0   -> hold nothing while idle, so there are no
                #                 connections to go stale in the first place.
                # check=...    -> validate a connection before handing it out;
                #                 a dead one is discarded and replaced instead
                #                 of being served to a request.
                # max_idle     -> retire idle connections well before Neon
                #                 would suspend under them.
                # max_lifetime -> bound total connection age.
                # timeout=15   -> fail fast rather than hanging 30s; a Neon
                #                 cold resume takes a few seconds, not thirty.
                min_size=0,
                max_size=5,
                # Neon's free tier suspends the compute when idle, and a cold
                # resume was measured at up to ~36s (deliberately suspended
                # the endpoint and timed it). A shorter wait makes the pool
                # give up *while the database is still booting* -- the
                # connection attempt itself is what triggers the wake, so
                # timing out here means every request after an idle period
                # 500s even though nothing is actually broken. Wait longer
                # than the worst observed resume, and stay under gunicorn's
                # 60s request timeout.
                timeout=45.0,
                # Don't churn connections every minute; that forced a cold
                # connect on almost every request after a quiet spell.
                max_idle=300.0,
                max_lifetime=1800.0,
                check=ConnectionPool.check_connection,
                kwargs={
                    "connect_timeout": 30,
                    "row_factory": dict_row,
                    "autocommit": False,
                    # Force the session timezone to UTC. Postgres always
                    # stores TIMESTAMPTZ internally as UTC, but renders it
                    # back to Python (and would render it in SQL text) in
                    # whatever the session's TimeZone setting is -- without
                    # this, a datetime read back from the same row as it was
                    # written could show a non-UTC offset (e.g. the host
                    # machine's local zone), which is confusing even though
                    # it's the same instant. Keeps the "timezone-aware UTC
                    # throughout" rule visibly true, not just technically true.
                    "options": "-c TimeZone=UTC",
                    # Disable psycopg3's automatic server-side prepared
                    # statements (default: PREPARE after the same query text
                    # has run 5 times on a connection -- `prepare_threshold`).
                    # This app has no control over which Neon endpoint ends
                    # up in DATABASE_URL: the operator may reasonably set it
                    # to Neon's pooled (`-pooler`, PgBouncer-style
                    # transaction-mode) endpoint, which is in fact the
                    # deployed configuration. Transaction-mode poolers are
                    # free to serve a client's next transaction from a
                    # *different* backend server process than the one that
                    # served its last -- a server-side PREPARE issued on
                    # backend A is simply not there when psycopg later sends
                    # EXECUTE for it against backend B, raising
                    # "prepared statement ... does not exist". A pool of
                    # sequential/lightly-concurrent connections opened during
                    # testing may never observe this (Neon's pooler can stay
                    # sticky to one backend when there's no contention for
                    # it -- confirmed empirically: pg_backend_pid() stayed
                    # constant across dozens of getconn/putconn cycles, both
                    # sequential and with 8 concurrent threads against this
                    # 5-connection pool), which is exactly what makes this
                    # bug so dangerous: it can pass every test and then
                    # surface only under real, higher-concurrency production
                    # traffic. Setting prepare_threshold=None makes every
                    # query a plain (unprepared) parameterised EXECUTE --
                    # still fully parameterised/SQL-injection-safe, just
                    # without the server-side-PREPARE optimisation -- which
                    # is safe on both the pooled and direct Neon endpoints,
                    # so there's no need to branch on which one is in use.
                    "prepare_threshold": None,
                },
                open=True,
            )
            _pg_pools[database_url] = pool
        return pool


def close_pg_pool(database_url: str) -> None:
    """Close and forget the pool for a given DATABASE_URL. Not used by the
    app itself (pools live for the process lifetime); the test suite calls
    this in teardown after dropping a per-test database, since Postgres
    refuses to DROP DATABASE while connections are still open against it."""
    with _pg_pools_lock:
        pool = _pg_pools.pop(database_url, None)
    if pool is not None:
        pool.close()


class _PgConnAdapter:
    """Wraps a pooled psycopg connection to match the same `execute` /
    `commit` / `rollback` / `close` surface as `_SqliteConnAdapter`. `close`
    returns the connection to the pool rather than actually closing it."""

    __slots__ = ("_pool", "_conn")

    def __init__(self, pool):
        self._pool = pool
        self._conn = pool.getconn()

    def execute(self, sql, params=()):
        return self._conn.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._pool.putconn(self._conn)


# ---------------------------------------------------------------------------
# Public, backend-agnostic API (frozen contract -- see SPEC.md section 13)
# ---------------------------------------------------------------------------

def _using_postgres(cfg) -> bool:
    return bool(getattr(cfg, "database_url", ""))


def get_db():
    """Return the per-request connection, creating it on first use.

    Postgres when `DATABASE_URL` is configured (pooled connection, returned
    to the pool at teardown); SQLite otherwise (a plain file connection,
    closed at teardown). Both expose the same `execute`/`commit` surface and
    both return mapping-style rows (`row["col"]`).
    """
    if "db" not in flask.g:
        cfg = get_config()
        if _using_postgres(cfg):
            pool = _get_pg_pool(cfg.database_url)
            try:
                flask.g.db = _PgConnAdapter(pool)
            except Exception:
                # psycopg_pool reports an exhausted pool as a bare
                # PoolTimeout ("couldn't get a connection after N sec"),
                # which says nothing about *why* every connection attempt
                # failed -- DNS, routing, TLS and a rejected password all
                # look identical from here. Make one direct attempt purely
                # to capture the real error in the logs, then re-raise the
                # original. Only runs on the already-failing path.
                _diag = logging.getLogger(__name__)
                # Pool stats are what actually distinguish "every slot is
                # checked out and never returned" from "the pool cannot
                # create connections at all". Print them before anything
                # else, since both look identical from the traceback.
                try:
                    _diag.error("DB DIAGNOSTIC pool stats: %r", pool.get_stats())
                except Exception as _stats_exc:
                    _diag.error("DB DIAGNOSTIC: pool stats unavailable: %s", _stats_exc)
                try:
                    import psycopg as _psycopg
                    with _psycopg.connect(cfg.database_url, connect_timeout=10):
                        _diag.error(
                            "DB DIAGNOSTIC: pool timed out, but a direct "
                            "connection then succeeded. Most likely the "
                            "pool's own attempt woke a suspended compute "
                            "and gave up before it finished booting; a "
                            "genuinely exhausted pool is the other "
                            "possibility. Check pool stats to tell them "
                            "apart."
                        )
                except Exception as exc:
                    _diag.error(
                        "DB DIAGNOSTIC: direct connection failed -- %s: %s",
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
            # half-committed transaction sitting on a pooled connection that
            # gets reused by the next request. (psycopg_pool also resets
            # connections on return, but don't rely solely on that.)
            try:
                conn.rollback()
            except Exception:
                pass
        conn.close()


def init_db(app: flask.Flask) -> None:
    """Create the schema (idempotent) and register the per-request
    teardown. Safe to call on every boot."""
    cfg = get_config()
    if _using_postgres(cfg):
        pool = _get_pg_pool(cfg.database_url)
        conn = pool.getconn()
        try:
            conn.execute(PG_SCHEMA)
            conn.commit()
        finally:
            pool.putconn(conn)
    else:
        conn = sqlite3.connect(cfg.database_path)
        try:
            conn.executescript(SQLITE_SCHEMA)
            conn.commit()
        finally:
            conn.close()
    app.teardown_appcontext(close_db)
