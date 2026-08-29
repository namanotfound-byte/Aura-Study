"""SQLite connection management and schema for AuraStudy.

Plain stdlib sqlite3 -- no ORM. One connection per request, stashed on
`flask.g` and closed in a teardown handler. See spec section 4 for the schema.
"""
import datetime
import sqlite3

import flask

from .config import get_config

SCHEMA = """
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


def utcnow() -> datetime.datetime:
    """Timezone-aware current UTC time."""
    return datetime.datetime.now(datetime.timezone.utc)


def utcnow_iso() -> str:
    """ISO-8601 string, the format stored in every *_at column."""
    return utcnow().isoformat()


def parse_iso(value: str) -> datetime.datetime:
    """Parse an ISO-8601 string produced by `utcnow_iso()` back into a
    timezone-aware datetime."""
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _connect(database_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    """Return the per-request SQLite connection, creating it on first use."""
    if "db" not in flask.g:
        cfg = get_config()
        flask.g.db = _connect(cfg.database_path)
    return flask.g.db


def close_db(_exception=None):
    conn = flask.g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(app: flask.Flask) -> None:
    """Create the schema (idempotent) and register the per-request teardown."""
    cfg = get_config()
    conn = _connect(cfg.database_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    app.teardown_appcontext(close_db)
